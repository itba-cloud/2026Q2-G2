"""The API: prices, the competitive set, alerts and the presigned uploads.

It runs as a Lambda behind API Gateway (`handler` is the entry point), whose
JWT authorizer lets a request through only with a valid Cognito token:
/api/health and /api/ready are the only routes without one (infra/locals.tf,
`api_public_route_keys`). `GET /health` never touches the database. The
user is the token's `sub` (auth.py); the first GET /auth/me creates it with
no org, and POST /me/org gives it one, as its owner. The catalogue reads
(/feed-dates, /branches, /products) ask for an org like every other read:
the portal asks for it before anything else. What only the org's `owner`
does: /ingest/jobs and /org/members, where it invites the members (Cognito
sends them a temporary password). /admin/* answers only to the Cognito group
`platform-admin`, with or without an org.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

import boto3
import psycopg
from botocore.config import Config
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from mangum import Mangum
from psycopg_pool import PoolTimeout

import comparison
import db
import maintenance
import queries
import serving
from auth import (CurrentUser, Identity, current_identity, current_user, require_owner,
                  require_platform_admin)
from common import SETTINGS, BotoCoreError, ClientError, aws_client, configure_logging
from models import (CreateOrgRequest, CreateRuleRequest, CreateUploadRequest,
                    InviteMemberRequest, MerchantRequest,
                    ReplaceRuleProductsRequest, SubscribeBranchRequest, UpdateBranchRequest,
                    UpdateRuleRequest)
from precomputed import followed_branches

configure_logging()
log = logging.getLogger("api")

MAX_RADIUS_KM = float(os.getenv("MAX_RADIUS_KM", "10.0"))
UPLOAD_URL_TTL = 900
# The biggest price list S3 takes: the presigned POST's policy caps the
# file, so one that would not fit a worker never reaches the bucket. A list
# of 100,000 products is about 5 MB.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# The invitation's Cognito half, a Lambda outside the VPC (app/invite).
INVITE_FUNCTION_NAME = os.getenv("INVITE_FUNCTION_NAME", "")


def presigning_s3_client() -> Any:
    """A presigned URL is only useful if whoever receives it can reach the
    host baked into its signature. In AWS that host is the same S3 endpoint
    the backend itself calls. Locally the backend talks to floci over the
    Docker network (AWS_ENDPOINT_URL=http://floci:4566), but a browser on the
    host can only reach floci through the port compose publishes there
    (http://localhost:4566) -- so presigning needs its own client, pointed at
    AWS_PUBLIC_ENDPOINT_URL, unset (and so ignored) in AWS.
    """
    endpoint = os.getenv("AWS_PUBLIC_ENDPOINT_URL") or SETTINGS.endpoint_url
    addressing = "path" if endpoint else "auto"
    # SigV4 explicitly: botocore may otherwise sign with SigV2, whose string
    # to sign takes headers the browser adds on its own.
    return boto3.client("s3", region_name=SETTINGS.region, endpoint_url=endpoint,
                        config=Config(signature_version="s3v4",
                                      s3={"addressing_style": addressing}))


# No lifespan: the pools open on the first request that asks for a
# connection (db.get_connection), so GET /health answers without the
# database.
app = FastAPI(title="LGDC API")


# The CORS preflights reach the API through their own public route (a
# browser sends them without a token). API Gateway adds the CORS headers to
# every response (infra/modules/api); the preflight only needs a 2xx.
@app.options("/{path:path}", status_code=204)
def preflight(path: str) -> None:
    return None

# Every route lives under this prefix: the portal calls <api>/api/*, and the
# callback has its own route next to it (/auth/callback).
router = APIRouter(prefix="/api")


def get_conn() -> Any:
    with db.get_connection() as connection:
        yield connection


def get_conn_ro() -> Any:
    with db.get_connection(readonly=True) as connection:
        yield connection


Conn = Annotated[Any, Depends(get_conn)]
# The read endpoints. Same shape as Conn, different instance behind it: with
# DB_HOST_RO set these go to the replica, so the portal's expensive aggregates
# are not competing with the ingestion's COPY on the primary. With it unset
# (local, Floci) it is the same pool and there is nothing to notice.
ReadConn = Annotated[Any, Depends(get_conn_ro)]

# Why several endpoints take BOTH: the heavy query reads only pipeline data
# (price, branch, branch_neighbor, product), where replica lag just means
# slightly stale prices -- which is what a daily feed is anyway. But the
# tenancy guard reads org_branch, which the user may have written *seconds*
# ago by subscribing to the branch they are now looking at. Answering that
# from the replica means telling someone who just subscribed that they have
# not. So the guard goes to the primary and the aggregate to the replica.
#
# Worst case for that lag is the ingestion window, when the primary is COPYing
# 14.5M rows -- exactly when someone is most likely to be clicking around.
# The Cognito user, with or without an org: only the onboarding takes it.
IdentityAuth = Annotated[Identity, Depends(current_identity)]
# A user with an org: every other endpoint.
Auth = Annotated[CurrentUser, Depends(current_user)]
# Logged in *and* the org's owner. The three endpoints that used to check
# `user.role != "owner"` by hand take this instead, so the rule is declared in
# the signature rather than re-remembered in the body.
OwnerAuth = Annotated[CurrentUser, Depends(require_owner)]
# In Cognito's platform-admin group: above the orgs, so no org needed.
PlatformAdminAuth = Annotated[Identity, Depends(require_platform_admin)]


def _branch_or_404(connection: Any, org_id: int, company_id: int, banner_id: int,
                   branch_id: str) -> dict[str, Any]:
    org_branch = queries.get_org_branch(connection, org_id, company_id, banner_id, branch_id)
    if org_branch is None:
        raise HTTPException(404, "branch not subscribed by this org")
    return org_branch


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "org"
    return f"{base}-{uuid.uuid4().hex[:6]}"


# -- Ops ------------------------------------------------------------------

@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Well under the Lambda's 30 s, so an unreachable database is a 503 from here
# and not a timeout from API Gateway.
READY_TIMEOUT_SECONDS = 5.0


@router.get("/ready")
def ready() -> dict[str, str]:
    """OK only if the database answers, through the Proxy in AWS."""
    try:
        with db.get_connection(timeout=READY_TIMEOUT_SECONDS) as connection:
            connection.execute("SELECT 1")
    # The AWS errors are the password's: the first connection reads it from
    # Secrets Manager.
    except (PoolTimeout, psycopg.Error, BotoCoreError, ClientError) as error:
        log.warning("not ready: %s", error)
        raise HTTPException(503, "database unreachable") from error
    return {"status": "ready"}


@router.get("/feed-dates")
def get_feed_dates(connection: ReadConn, user: Auth) -> list[str]:
    return queries.feed_dates(connection)


@router.get("/ingest/jobs")
def ingest_jobs(connection: ReadConn, owner: OwnerAuth, feed_date: str | None = None,
                status: str | None = None, limit: int = Query(100, le=500)) -> list[dict]:
    """El tablero de la ingesta, y el único lector es `/pipeline`.

    Pide `owner` y no sólo un token porque es lo mismo que ya afirman
    `Layout.tsx` (esconde la sección Administración) y `ProtectedRoute`
    (redirige `/pipeline` a la raíz cuando el rol no es `owner`): esconder el
    link no es proteger el dato, y un `member` con el token en la mano leía
    igual el estado de cada cadena. `/feed-dates` sigue abierto a cualquier
    sesión -- eso sí es catálogo compartido, y lo usan el tablero, Mis precios
    y Comparar.
    """
    return queries.pipeline_jobs(connection, feed_date, status, limit)


# -- Auth / tenancy ---------------------------------------------------------

@router.get("/auth/me")
def me(connection: Conn, identity: IdentityAuth) -> dict[str, Any]:
    """The user behind the token, created on its first login with no org
    (`org_id` null): the portal then asks for the org's name. Also records
    the login."""
    return queries.login_user(connection, identity.sub, identity.email)


@router.post("/me/org", status_code=201)
def create_my_org(body: CreateOrgRequest, connection: Conn,
                  identity: IdentityAuth) -> dict[str, Any]:
    """The first login's onboarding: the org, with the user as its owner.
    409 if the user already has one."""
    user = queries.get_user_by_sub(connection, identity.sub)
    if user is None:
        raise HTTPException(409, "log in first: GET /auth/me")
    if user["org_id"] is not None:
        raise HTTPException(409, "the user already has an organization")
    with connection.transaction():
        org_id = queries.create_org(connection, body.name, _slug(body.name))
        if not queries.join_org(connection, user["user_id"], org_id, "owner"):
            # Another request gave it one first.
            raise HTTPException(409, "the user already has an organization")
    return queries.get_user(connection, user["user_id"])


@router.get("/org/members")
def org_members(connection: Conn, owner: OwnerAuth) -> list[dict[str, Any]]:
    """The org's users and their roles, the owner first."""
    return queries.list_members(connection, owner.org_id)


@router.post("/org/members", status_code=201)
def invite_member(body: InviteMemberRequest, connection: Conn,
                  owner: OwnerAuth) -> dict[str, Any]:
    """Invites a member into the owner's org: anyone with no org yet. 409 if
    the email is already in an org (this one or another).

    A new email gets its Cognito user, and Cognito emails it a temporary
    password (`invitation_sent`). An email Cognito already has (someone who
    signed up and never named an org, or an invitation whose answer got lost
    on the way back) joins as it is, with no email: it logs in with its own
    password. Either way the row goes first, inside a transaction, so an
    email already in an org is a 409 before anything reaches Cognito, and if
    Cognito says no, the row is rolled back."""
    email = body.email.lower()
    with connection.transaction():
        user_id = queries.add_member(connection, owner.org_id, email)
        if user_id is None:
            raise HTTPException(409, "that email is already in an organization")
        invited = invite_in_cognito(email)
        if not queries.set_cognito_sub(connection, user_id, invited.sub):
            # The Cognito user is another row's: its email changed since.
            raise HTTPException(409, "that Cognito user is already in LGDC with another email")
    return {**queries.get_member(connection, owner.org_id, user_id),
            "invitation_sent": invited.invitation_sent}


@dataclass(frozen=True)
class CognitoUser:
    """What the invite Lambda answers: the user's sub, and whether Cognito
    emailed it a temporary password (only to a user it just created)."""
    sub: str
    invitation_sent: bool


@functools.cache
def lambda_client() -> Any:
    # A retry is safe: the second call finds the user the first created. Two
    # attempts that wait for the invite Lambda (10 s) stay under the API's
    # own 30 s. The retry is mostly for the endpoint closing a keep-alive
    # connection that a frozen Lambda kept in its pool.
    return boto3.client("lambda", region_name=SETTINGS.region, config=Config(
        connect_timeout=3, read_timeout=11,
        retries={"mode": "standard", "total_max_attempts": 2}))


def invite_in_cognito(email: str) -> CognitoUser:
    """Invokes the invite Lambda (app/invite) and waits for it."""
    if not INVITE_FUNCTION_NAME:
        raise HTTPException(503, "invitations are not configured (INVITE_FUNCTION_NAME)")
    try:
        response = lambda_client().invoke(
            FunctionName=INVITE_FUNCTION_NAME, Payload=json.dumps({"email": email}).encode())
        result = json.loads(response["Payload"].read())
    except (BotoCoreError, ClientError) as error:
        log.error("could not invoke %s: %s", INVITE_FUNCTION_NAME, error)
        raise HTTPException(502, "could not reach Cognito") from error
    if response.get("FunctionError"):
        log.error("%s failed: %s", INVITE_FUNCTION_NAME, result)
        raise HTTPException(502, "Cognito did not create the user")
    if result["status"] == "invalid":
        raise HTTPException(422, "Cognito refused that email")
    return CognitoUser(result["sub"], invitation_sent=result["status"] == "created")


@router.get("/me/merchant")
def my_merchant(connection: Conn, user: Auth) -> dict[str, Any] | None:
    """La sucursal propia de la org, o `null` si todavía no la dio de alta.

    `null` y no un 404: "todavía no tengo sucursal propia" es una respuesta
    válida a esta pregunta, no un error, y es lo que la pantalla de carga
    necesita para decidir si muestra el alta o el formulario de archivo. Un
    404 la obligaría a tratar el caso normal como excepción.
    """
    return queries.get_merchant(connection, user.org_id)


@router.post("/me/merchant")
def set_merchant(body: MerchantRequest, connection: Conn, user: Auth) -> dict[str, Any]:
    """Da de alta (o edita) la sucursal propia de la org, para un comercio que
    no publica en el SEPA. Es lo que `POST /uploads` exige antes de aceptar un
    archivo, porque la ubicación de esa sucursal es lo que define contra quién
    se compara. Ver app/batch/worker/upload.py.

    Pide sólo sesión, no `owner`. Era owner-only y no tenía por qué: subir la
    lista de precios propia (`POST /uploads`) siempre estuvo abierta a
    cualquier usuario, así que el rol sólo servía para dejar a un `member` a
    mitad de camino -- con permiso para subir el archivo y sin permiso para
    crear la sucursal a la que ese archivo pertenece. Lo único que hoy
    distingue a un `owner` es invitar miembros (`/org/members`) y el
    tablero de la ingesta; `/admin/*` es del admin de plataforma.

    Sus vecinos se calculan acá, en la misma transacción: si esperaran al
    próximo cierre del día, la primera carga se compararía contra nadie. Si
    ya tenía precios cargados (la mueve de lugar), se vuelve a encolar su
    precálculo con los vecinos nuevos.
    """
    with connection.transaction():
        merchant = queries.upsert_merchant(
            connection, user.org_id, body.name, body.province, body.locality,
            body.latitude, body.longitude)
        maintenance.refresh_branch_neighbors(connection, merchant["company_id"],
                                             merchant["banner_id"], merchant["branch_id"])
    last_day = queries.latest_upload_feed_date(connection, merchant["company_id"])
    if last_day is not None:
        serving.queue_precompute(last_day, followed_branches(connection, merchant["company_id"]))
    return merchant


# -- Branches ---------------------------------------------------------------

@router.get("/branches")
def get_branches(connection: ReadConn, user: Auth, q: str | None = None,
                 province: str | None = None, limit: int = Query(50, le=200),
                 offset: int = 0) -> list[dict]:
    return queries.search_branches(connection, q, province, limit, offset)


@router.get("/branches/{company_id}/{banner_id}/{branch_id}")
def get_branch(connection: ReadConn, user: Auth, company_id: int, banner_id: int,
               branch_id: str) -> dict[str, Any]:
    branch = queries.get_branch(connection, company_id, banner_id, branch_id)
    if branch is None:
        raise HTTPException(404, "branch not found")
    return branch


@router.get("/me/branches")
def my_branches(connection: Conn, user: Auth) -> list[dict]:
    return queries.list_org_branches(connection, user.org_id)


@router.post("/me/branches", status_code=201)
def add_my_branch(body: SubscribeBranchRequest, connection: Conn, user: Auth) -> dict:
    if queries.get_branch(connection, body.company_id, body.banner_id,
                          body.branch_id) is None:
        raise HTTPException(404, "branch not found")
    queries.subscribe_branch(connection, user.org_id, body.company_id, body.banner_id,
                             body.branch_id, body.label, body.radius_km)
    return _branch_or_404(connection, user.org_id, body.company_id, body.banner_id,
                          body.branch_id)


@router.patch("/me/branches/{company_id}/{banner_id}/{branch_id}")
def patch_my_branch(body: UpdateBranchRequest, connection: Conn, user: Auth,
                    company_id: int, banner_id: int, branch_id: str) -> dict:
    _branch_or_404(connection, user.org_id, company_id, banner_id, branch_id)
    queries.subscribe_branch(connection, user.org_id, company_id, banner_id, branch_id,
                             body.label, body.radius_km)
    return _branch_or_404(connection, user.org_id, company_id, banner_id, branch_id)


@router.delete("/me/branches/{company_id}/{banner_id}/{branch_id}", status_code=204)
def remove_my_branch(connection: Conn, user: Auth, company_id: int, banner_id: int,
                     branch_id: str) -> None:
    queries.unsubscribe_branch(connection, user.org_id, company_id, banner_id, branch_id)


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/prices")
def get_branch_prices(connection: ReadConn, primary: Conn, user: Auth,
                      company_id: int, banner_id: int,
                      branch_id: str, feed_date: str | None = None, q: str | None = None,
                      limit: int = Query(100, le=500), offset: int = 0) -> list[dict]:
    _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    resolved = feed_date or queries.latest_feed_date(connection)
    if resolved is None:
        return []
    return queries.branch_prices(connection, company_id, banner_id, branch_id,
                                 resolved, q, limit, offset)


# -- Products -----------------------------------------------------------------

@router.get("/products")
def get_products(connection: ReadConn, user: Auth, q: str = Query(min_length=2),
                 limit: int = Query(30, le=100), offset: int = 0) -> list[dict]:
    return queries.search_products(connection, q, limit, offset)


@router.get("/products/{ean}")
def get_product(connection: ReadConn, user: Auth, ean: str) -> dict[str, Any]:
    product = queries.get_product(connection, ean)
    if product is None:
        raise HTTPException(404, "product not found")
    return product


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/products/{ean}/competitors")
def get_product_competitor_prices(connection: ReadConn, primary: Conn, user: Auth,
                                  company_id: int,
                                  banner_id: int, branch_id: str, ean: str,
                                  feed_date: str | None = None,
                                  radius_km: float | None = None,
                                  limit: int = Query(50, le=200)) -> list[dict]:
    """What each competitor inside the radius charges for one barcode."""
    org_branch = _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    radius = min(radius_km or float(org_branch["radius_km"]), MAX_RADIUS_KM)
    resolved = feed_date or queries.latest_feed_date(connection)
    if resolved is None:
        return []
    return queries.product_competitor_prices(connection, company_id, banner_id, branch_id,
                                             ean, resolved, radius, limit)


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/products/{ean}/history")
def get_product_history(connection: ReadConn, primary: Conn, user: Auth,
                        company_id: int, banner_id: int,
                        branch_id: str, ean: str, days: int = Query(60, le=180)) -> list[dict]:
    _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    dates = [d for d in queries.feed_dates(connection)][:days]
    if not dates:
        return []
    return queries.product_history(connection, ean, company_id, banner_id, branch_id, dates)


# -- Conjunto competitivo -------------------------------------------------------

@router.get("/branches/{company_id}/{banner_id}/{branch_id}/competitors")
def get_competitors(connection: ReadConn, primary: Conn, user: Auth,
                    company_id: int, banner_id: int,
                    branch_id: str, radius_km: float | None = None,
                    q: str | None = None, limit: int = Query(50, le=500),
                    offset: int = 0) -> dict[str, Any]:
    """The branches of other chains inside the radius, nearest first.

    Returns `{total, items}`, not a bare list: `total` is how many the radius
    holds, `items` the page asked for. Both halves have a caller -- the count
    is on screen next to the radius control, and paging without it would mean
    a "siguiente" button that can only guess whether there is a next page.

    `le=500` and not the 200 the other read endpoints use because one caller
    is a map: markers are drawn all at once, so it pages by not paging, and
    500 is where drawing them stops being free.
    """
    org_branch = _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    radius = min(radius_km or float(org_branch["radius_km"]), MAX_RADIUS_KM)
    return queries.competitors(connection, company_id, banner_id, branch_id, radius,
                               q, limit, offset)


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/comparison")
def get_comparison(connection: ReadConn, primary: Conn, user: Auth,
                   company_id: int, banner_id: int,
                   branch_id: str, feed_date: str | None = None,
                   radius_km: float | None = None, q: str | None = None,
                   limit: int = Query(50, le=200),
                   cursor: str | None = None) -> dict[str, Any]:
    """Mis Precios: a page of the comparison, worst gap first, filtered by
    description with `q`. Returns `{feed_date, source, items, next_cursor}`:
    `next_cursor`, opaque, asks for the next page, and is null on the last.
    From the precompute at the configured radius, live otherwise
    (serving.py)."""
    org_branch = _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    read = serving.plan(org_branch, company_id, banner_id, branch_id, feed_date, radius_km,
                        MAX_RADIUS_KM)
    return serving.comparison_page(connection, read, feed_date, q, limit, cursor)


MAX_HEAD_TO_HEAD_TARGETS = 4


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/head-to-head")
def get_head_to_head(connection: ReadConn, primary: Conn, user: Auth,
                     company_id: int, banner_id: int,
                     branch_id: str, vs: list[str] = Query(default=[]),
                     feed_date: str | None = None, q: str | None = None,
                     limit: int = Query(50, le=200), offset: int = 0) -> list[dict]:
    """Your prices against named branches, rather than against whatever the
    radius happens to catch. Each `vs` is "company_id:banner_id:branch_id"."""
    _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    if not vs:
        raise HTTPException(400, "pass at least one vs=company_id:banner_id:branch_id")
    if len(vs) > MAX_HEAD_TO_HEAD_TARGETS:
        raise HTTPException(400, f"at most {MAX_HEAD_TO_HEAD_TARGETS} branches at a time")

    targets: list[tuple[int, int, str]] = []
    for raw in vs:
        parts = raw.split(":")
        if len(parts) != 3:
            raise HTTPException(400, f"malformed vs value: {raw}")
        try:
            targets.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            raise HTTPException(400, f"malformed vs value: {raw}")

    resolved = feed_date or queries.latest_feed_date(connection)
    if resolved is None:
        return []
    return queries.head_to_head(connection, company_id, banner_id, branch_id,
                                resolved, targets, q, limit, offset)


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/comparison/summary")
def get_comparison_summary(connection: ReadConn, primary: Conn, user: Auth,
                           company_id: int, banner_id: int,
                           branch_id: str, feed_date: str | None = None,
                           radius_km: float | None = None) -> dict[str, Any]:
    """The position summary, always from the live query."""
    org_branch = _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    radius = min(radius_km or float(org_branch["radius_km"]), MAX_RADIUS_KM)
    resolved = feed_date or queries.latest_feed_date(connection)
    if resolved is None:
        return comparison.empty_summary()
    return comparison.summary(connection, company_id, banner_id, branch_id, resolved, radius)


DASHBOARD_ROWS = 20


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/dashboard")
def get_dashboard(connection: ReadConn, primary: Conn, user: Auth,
                  company_id: int, banner_id: int, branch_id: str,
                  feed_date: str | None = None, radius_km: float | None = None,
                  limit: int = Query(DASHBOARD_ROWS, le=100)) -> dict[str, Any]:
    """Everything the tablero puts on screen: `{feed_date, source, summary,
    worst}`, the position summary and the worst `limit` rows.

    At the branch's configured radius it reads the precompute, the last
    complete day of it; another radius or day, or a branch with no precompute
    yet, gets the live query, which also queues the precompute in the last
    case (serving.py). `source` says which.

    `feed_date` is resolved here rather than by the caller, and the answer
    says which day it is: the screen labels itself from it.
    """
    org_branch = _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    read = serving.plan(org_branch, company_id, banner_id, branch_id, feed_date, radius_km,
                        MAX_RADIUS_KM)
    return serving.dashboard(connection, read, feed_date, limit)


@router.get("/branches/{company_id}/{banner_id}/{branch_id}/changes")
def get_branch_changes(connection: ReadConn, primary: Conn, user: Auth,
                       company_id: int, banner_id: int,
                       branch_id: str, feed_date: str | None = None, min_pct: float = 0.0,
                       limit: int = Query(50, le=200)) -> list[dict]:
    _branch_or_404(primary, user.org_id, company_id, banner_id, branch_id)
    resolved = feed_date or queries.latest_feed_date(connection)
    if resolved is None:
        return []
    return queries.branch_changes(connection, company_id, banner_id, branch_id, resolved,
                                  min_pct, limit)


# -- Alertas ------------------------------------------------------------------

@router.get("/alerts")
def get_alerts(connection: ReadConn, user: Auth, unread_only: bool = False,
               limit: int = Query(50, le=200), offset: int = 0) -> list[dict]:
    return queries.list_alerts(connection, user.org_id, unread_only, limit, offset)


@router.get("/alerts/unread-count")
def get_unread_count(connection: ReadConn, user: Auth) -> dict[str, int]:
    return {"count": queries.unread_alert_count(connection, user.org_id)}


@router.post("/alerts/{alert_id}/read")
def read_alert(connection: Conn, user: Auth, alert_id: int) -> dict[str, bool]:
    ok = queries.mark_alert_read(connection, user.org_id, alert_id)
    if not ok:
        raise HTTPException(404, "alert not found")
    return {"ok": True}


@router.post("/alerts/read-all")
def read_all_alerts(connection: Conn, user: Auth) -> dict[str, int]:
    return {"marked": queries.mark_all_alerts_read(connection, user.org_id)}


@router.get("/rules")
def get_rules(connection: Conn, user: Auth) -> list[dict]:
    return queries.list_rules(connection, user.org_id)


@router.post("/rules", status_code=201)
def add_rule(body: CreateRuleRequest, connection: Conn, user: Auth) -> dict[str, Any]:
    _branch_or_404(connection, user.org_id, body.company_id, body.banner_id, body.branch_id)
    rule_id = queries.create_rule(
        connection, user.org_id, body.name, body.company_id, body.banner_id,
        body.branch_id, body.radius_km, body.target_company_id, body.target_banner_id,
        body.target_branch_id, body.threshold_pct, body.products)
    return {"rule_id": rule_id}


@router.patch("/rules/{rule_id}")
def patch_rule(body: UpdateRuleRequest, connection: Conn, user: Auth,
               rule_id: int) -> dict[str, bool]:
    ok = queries.set_rule_enabled(connection, user.org_id, rule_id, body.enabled)
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.delete("/rules/{rule_id}", status_code=204)
def remove_rule(connection: Conn, user: Auth, rule_id: int) -> None:
    if not queries.delete_rule(connection, user.org_id, rule_id):
        raise HTTPException(404, "rule not found")


@router.put("/rules/{rule_id}/products")
def put_rule_products(body: ReplaceRuleProductsRequest, connection: Conn, user: Auth,
                      rule_id: int) -> dict[str, bool]:
    ok = queries.replace_rule_products(connection, user.org_id, rule_id, body.products)
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"ok": True}


# -- Carga propia ---------------------------------------------------------------

@router.post("/uploads", status_code=201)
def create_upload(body: CreateUploadRequest, connection: Conn, user: Auth) -> dict[str, Any]:
    """Create the upload and hand back a presigned POST the browser uploads
    straight to S3 with: the file never passes through the API. That is all
    the API does. The file lands in raw/uploads/<upload_id>/, S3 announces
    it to the jobs queue like a chain's file, and the worker that takes the
    message creates the job and links it to this upload.

    A POST and not a PUT: its policy, which S3 enforces, names the one key
    the browser may write and caps the file's size."""
    merchant = queries.get_merchant(connection, user.org_id)
    if merchant is None:
        raise HTTPException(400, "set up your merchant profile first via POST /me/merchant")
    try:
        date.fromisoformat(body.feed_date)
    except ValueError:
        raise HTTPException(422, "feed_date must be YYYY-MM-DD")
    # The name's last segment: a slash would put the file where the worker
    # does not look for an upload. And no '$': S3 reads `${filename}` in a
    # POST's key as the uploaded file's own name.
    filename = re.split(r"[/\\]", body.filename)[-1].strip().replace("$", "_")
    if not filename:
        raise HTTPException(422, "filename must name a file")

    upload_id = str(uuid.uuid4())
    s3_key = f"raw/uploads/{upload_id}/{filename}"
    queries.create_upload(connection, upload_id, user.org_id, merchant["company_id"],
                          body.feed_date, s3_key, filename, user.user_id)

    presigned = presigning_s3_client().generate_presigned_post(
        Bucket=SETTINGS.bucket, Key=s3_key,
        Conditions=[["content-length-range", 1, MAX_UPLOAD_BYTES]],
        ExpiresIn=UPLOAD_URL_TTL)
    return {"upload_id": upload_id, "url": presigned["url"], "fields": presigned["fields"],
            "expires_in": UPLOAD_URL_TTL, "max_bytes": MAX_UPLOAD_BYTES}


@router.delete("/uploads/{upload_id}")
def remove_upload(connection: Conn, user: Auth, upload_id: str) -> dict[str, Any]:
    """Borra una carga, su archivo en S3 y los precios que dejó.

    La base primero y S3 después, y el borrado del objeto es best effort. Las
    dos formas de fallar a mitad de camino no son equivalentes: si S3 se borra
    y la base no, queda una carga listada que apunta a un archivo que no
    existe y que no se puede reprocesar; al revés, queda un objeto huérfano
    que nadie lista y que la regla de ciclo de vida del bucket termina
    limpiando. El huérfano es el barato.
    """
    borrado = queries.delete_upload(connection, user.org_id, upload_id)
    if borrado is None:
        raise HTTPException(404, "upload not found")
    if borrado == queries.UPLOAD_IN_PROGRESS:
        # A worker is loading it: deleting now would leave its prices loaded
        # with no upload to delete them from.
        raise HTTPException(409, "upload is being processed")
    try:
        aws_client("s3").delete_object(Bucket=SETTINGS.bucket, Key=borrado["s3_key"])
    except ClientError as error:
        log.warning("carga %s borrada, pero su objeto %s quedó en S3 (%s)",
                    upload_id, borrado["s3_key"], error)
    if borrado["precios"]:
        # The precompute of that day still has the deleted prices: it is
        # computed again, as when the upload loaded.
        serving.queue_precompute(str(borrado["feed_date"]),
                                 followed_branches(connection, borrado["company_id"]))
    return borrado


@router.get("/uploads")
def get_uploads(connection: Conn, user: Auth, limit: int = Query(50, le=200)) -> list[dict]:
    return queries.list_uploads(connection, user.org_id, limit)


@router.get("/uploads/{upload_id}")
def get_upload(connection: Conn, user: Auth, upload_id: str) -> dict[str, Any]:
    row = queries.get_upload(connection, user.org_id, upload_id)
    if row is None:
        raise HTTPException(404, "upload not found")
    return row


@router.get("/uploads/{upload_id}/rejected")
def get_upload_rejected(connection: Conn, user: Auth, upload_id: str,
                        limit: int = Query(500, le=5000)) -> list[dict[str, Any]]:
    """The rows the worker could not load, and why: line, reason and what the
    line said. They live in S3, next to the job's other outputs
    (rejected/uploads/<upload_id>/rejected.jsonl); an upload with none, or
    not processed yet, has an empty list."""
    if queries.get_upload(connection, user.org_id, upload_id) is None:
        raise HTTPException(404, "upload not found")
    try:
        stored = aws_client("s3").get_object(
            Bucket=SETTINGS.bucket, Key=f"rejected/uploads/{upload_id}/rejected.jsonl")
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "NoSuchKey":
            return []
        raise
    lines = stored["Body"].read().decode().splitlines()
    return [json.loads(line) for line in lines[:limit] if line]


# -- Admin: maintenance the worker deliberately does not run per-job ------------
#
# Both operate on data shared by every org (branch_neighbor, product_norm) --
# `refresh_neighbors` starts with a TRUNCATE that wipes it for everyone -- so
# no org's owner may fire them: only the platform admin.

# The whole rebuild takes longer than API Gateway waits (30 s), so it runs in
# the day close Lambda (app/batch/day_close), asynchronously, which also
# queues the precompute of every followed branch with the new pairs. 202:
# the rebuild has started, not finished.
DAY_CLOSE_FUNCTION_NAME = os.getenv("DAY_CLOSE_FUNCTION_NAME", "")


@router.post("/admin/neighbors/refresh", status_code=202)
def refresh_neighbors(platform_admin: PlatformAdminAuth) -> dict[str, str]:
    if not DAY_CLOSE_FUNCTION_NAME:
        raise HTTPException(503, "the rebuild is not configured (DAY_CLOSE_FUNCTION_NAME)")
    try:
        lambda_client().invoke(FunctionName=DAY_CLOSE_FUNCTION_NAME, InvocationType="Event",
                               Payload=json.dumps({"action": "refresh_neighbors"}).encode())
    except (BotoCoreError, ClientError) as error:
        log.error("could not invoke %s: %s", DAY_CLOSE_FUNCTION_NAME, error)
        raise HTTPException(502, "could not start the rebuild") from error
    return {"status": "started"}


@router.post("/admin/catalog/normalize")
def normalize_catalog(connection: Conn, platform_admin: PlatformAdminAuth) -> dict[str, int]:
    return {"products": maintenance.normalize_catalog(connection)}


app.include_router(router)

# The Lambda entry point. lifespan="off" keeps a cold start from dialing the
# database: db.get_connection() opens the pools on first use instead, so
# GET /api/health answers even when RDS is unreachable. Mangum keeps the
# event in the scope (`aws.event`), where auth.py reads the token's claims.
handler = Mangum(app, lifespan="off")
