"""All the backend's SQL, as functions over a connection.

Kept separate from `main.py` the way `app/batch/worker/sepa.py` is kept separate
from `app/batch/worker/main.py`: this module is what actually reads and writes rows; `main.py`
is HTTP wiring around it. Every function takes a psycopg connection (pooled,
autocommit, dict_row -- see db.py) as its first argument.
"""
from __future__ import annotations

from typing import Any

import psycopg

from comparison import IMPLAUSIBLE_PRICE_FLOOR, MAX_CREDIBLE_GAP_PCT, OUTLIER_RATIO

# -- Feed dates and the pipeline board ---------------------------------------

# The days the SEPA chains loaded: what every comparison is against. A
# merchant's own list does not make a day: dated after the SEPA's latest, it
# made that date "the latest" for every org, and every branch compared on it
# against chains with no prices that day (0 compared).
FEED_DATES = """
    SELECT DISTINCT feed_date FROM ingest_job
    WHERE status = 'done' AND kind = 'sepa' ORDER BY feed_date DESC
"""


def feed_dates(connection: Any) -> list[str]:
    return [str(row["feed_date"]) for row in connection.execute(FEED_DATES).fetchall()]


def latest_feed_date(connection: Any) -> str | None:
    dates = feed_dates(connection)
    return dates[0] if dates else None


def latest_upload_feed_date(connection: Any, company_id: int) -> str | None:
    """The last day a merchant's own list was loaded for. From its uploads,
    not from `price`, whose indexes lead with feed_date."""
    row = connection.execute("""
        SELECT max(feed_date)::text AS day FROM price_upload
        WHERE company_id = %s AND status = 'done'
    """, (company_id,)).fetchone()
    return row["day"] if row else None


def pipeline_jobs(connection: Any, feed_date: str | None, status: str | None,
                  limit: int) -> list[dict[str, Any]]:
    clauses, params = [], []
    if feed_date:
        clauses.append("feed_date = %s")
        params.append(feed_date)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    # The chain's name next to its id: a merchant's own name for an upload,
    # the first banner a SEPA chain loaded for the rest. A chain whose first
    # file failed has no branch yet, and no name.
    return connection.execute(f"""
        SELECT j.job_id, j.feed_date, j.chain_id, j.kind, j.status, j.worker_id,
               j.attempts, j.rows_ok, j.rows_rejected, j.last_error, j.created_at,
               j.updated_at, coalesce(m.name, b.banner_name) AS chain_name
        FROM (SELECT *,
                     -- Cast only what is a company id: a stray chain_id must
                     -- not fail the whole board.
                     CASE WHEN chain_id ~ '^[0-9]{{1,9}}$' THEN chain_id::int END AS company_id
              FROM ingest_job {where}
              ORDER BY created_at DESC LIMIT %s) j
        LEFT JOIN merchant_source m
          ON j.kind = 'upload' AND m.company_id = j.company_id
        LEFT JOIN LATERAL (
            SELECT banner_name FROM branch
            WHERE j.kind = 'sepa' AND company_id = j.company_id
            ORDER BY banner_id, branch_id LIMIT 1) b ON true
        ORDER BY j.created_at DESC
    """, params).fetchall()


# -- Branches -----------------------------------------------------------------

def search_branches(connection: Any, q: str | None, province: str | None,
                    limit: int, offset: int) -> list[dict[str, Any]]:
    clauses, params = [], []
    # Each word on its own, in any of the fields: "dia almagro" is a banner
    # and a branch name, and as one phrase it matched neither.
    for word in (q or "").split():
        clauses.append("concat_ws(' ', b.name, b.banner_name, b.legal_name, b.locality)"
                       " ILIKE %s")
        params.append(f"%{word}%")
    if province:
        clauses.append("b.province = %s")
        params.append(province)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params += [limit, offset]
    return connection.execute(f"""
        SELECT company_id, banner_id, branch_id, banner_name, legal_name, name,
               kind, province, locality, latitude, longitude
        FROM branch b {where}
        ORDER BY banner_name, name LIMIT %s OFFSET %s
    """, params).fetchall()


def get_branch(connection: Any, company_id: int, banner_id: int,
               branch_id: str) -> dict[str, Any] | None:
    return connection.execute("""
        SELECT company_id, banner_id, branch_id, banner_name, legal_name, name,
               kind, province, locality, latitude, longitude
        FROM branch WHERE company_id = %s AND banner_id = %s AND branch_id = %s
    """, (company_id, banner_id, branch_id)).fetchone()


def branch_prices(connection: Any, company_id: int, banner_id: int, branch_id: str,
                  feed_date: str, q: str | None, limit: int,
                  offset: int) -> list[dict[str, Any]]:
    """That branch's own price list for a date. This is the query the missing
    idx_price_branch index exists for -- see app/db_migrator/schema.py."""
    clauses = ["p.feed_date = %s", "p.company_id = %s", "p.banner_id = %s",
               "p.branch_id = %s"]
    params: list[Any] = [feed_date, company_id, banner_id, branch_id]
    if q:
        clauses.append("pr.description ILIKE %s")
        params.append(f"%{q}%")
    params += [limit, offset]
    return connection.execute(f"""
        SELECT p.ean, pr.description, pr.brand, pr.presentation, p.price
        FROM price p LEFT JOIN product pr ON pr.ean = p.ean
        WHERE {' AND '.join(clauses)}
        ORDER BY pr.description LIMIT %s OFFSET %s
    """, params).fetchall()


# -- Org-branch subscriptions --------------------------------------------------

def list_org_branches(connection: Any, org_id: int) -> list[dict[str, Any]]:
    return connection.execute("""
        SELECT ob.company_id, ob.banner_id, ob.branch_id, ob.label, ob.radius_km,
               b.name, b.banner_name, b.province, b.locality, b.latitude, b.longitude
        FROM org_branch ob JOIN branch b USING (company_id, banner_id, branch_id)
        WHERE ob.org_id = %s ORDER BY ob.created_at
    """, (org_id,)).fetchall()


def subscribe_branch(connection: Any, org_id: int, company_id: int, banner_id: int,
                     branch_id: str, label: str | None, radius_km: float) -> None:
    connection.execute("""
        INSERT INTO org_branch (org_id, company_id, banner_id, branch_id, label, radius_km)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (org_id, company_id, banner_id, branch_id)
          DO UPDATE SET label = EXCLUDED.label, radius_km = EXCLUDED.radius_km
    """, (org_id, company_id, banner_id, branch_id, label, radius_km))


def unsubscribe_branch(connection: Any, org_id: int, company_id: int, banner_id: int,
                       branch_id: str) -> None:
    connection.execute(
        "DELETE FROM org_branch WHERE org_id = %s AND company_id = %s "
        "AND banner_id = %s AND branch_id = %s",
        (org_id, company_id, banner_id, branch_id))


def get_org_branch(connection: Any, org_id: int, company_id: int, banner_id: int,
                   branch_id: str) -> dict[str, Any] | None:
    """Every branch-scoped endpoint checks this first: an org only ever sees
    the branches it explicitly subscribed to -- that is the whole tenancy
    boundary, and it lives in one place. Also the source of the branch's
    default radius, so a caller does not have to pass one every time."""
    return connection.execute(
        "SELECT company_id, banner_id, branch_id, label, radius_km FROM org_branch "
        "WHERE org_id = %s AND company_id = %s AND banner_id = %s AND branch_id = %s",
        (org_id, company_id, banner_id, branch_id)).fetchone()


# -- Products -------------------------------------------------------------------

def search_products(connection: Any, q: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    """Trigram search over the normalised catalogue. Falls back to a plain
    ILIKE over `product` when product_norm has not been populated yet (before
    the first POST /admin/catalog/normalize), so search still works, just
    without accent-folding, on a fresh install."""
    normalised = connection.execute(
        "SELECT ean, name_norm, brand_norm, size_value, size_unit FROM product_norm "
        # `%%` is the trigram similarity operator, doubled because psycopg
        # reads a lone `%` as the start of a placeholder.
        "WHERE name_norm %% f_unaccent(lower(%s)) "
        # `ean` breaks ties. Similarity alone is not a total order, and without
        # one, OFFSET can repeat a row on one page and skip it on the next.
        "ORDER BY similarity(name_norm, f_unaccent(lower(%s))) DESC, ean "
        "LIMIT %s OFFSET %s",
        (q, q, limit, offset)).fetchall()
    if normalised:
        return normalised
    # Only the first page may fall back: past the end of the trigram results an
    # empty page is the honest answer, not a second, unrelated result set.
    if offset > 0:
        return []
    return connection.execute(
        "SELECT ean, description AS name_norm, brand AS brand_norm, "
        "NULL::numeric AS size_value, NULL::text AS size_unit FROM product "
        "WHERE description ILIKE %s ORDER BY ean LIMIT %s", (f"%{q}%", limit)).fetchall()


def get_product(connection: Any, ean: str) -> dict[str, Any] | None:
    product = connection.execute(
        "SELECT ean, description, brand, presentation FROM product WHERE ean = %s",
        (ean,)).fetchone()
    if product is None:
        return None
    product["chains"] = connection.execute(
        "SELECT company_id, description, brand, presentation FROM product_name "
        "WHERE ean = %s ORDER BY company_id", (ean,)).fetchall()
    return product


def product_history(connection: Any, ean: str, company_id: int, banner_id: int,
                    branch_id: str, dates: list[str]) -> list[dict[str, Any]]:
    """Price trajectory across an explicit list of feed_dates. Binding dates as
    an array rather than a `feed_date >= from` range is what keeps this an
    index seek per date instead of a scan -- see app/db_migrator/schema.py's note on
    idx_price_branch."""
    return connection.execute("""
        SELECT feed_date, price FROM price
        WHERE feed_date = ANY(%s) AND ean = %s
          AND company_id = %s AND banner_id = %s AND branch_id = %s
        ORDER BY feed_date
    """, (dates, ean, company_id, banner_id, branch_id)).fetchall()


# -- Conjunto competitivo -------------------------------------------------------

# The radius' neighbours, as a page. Split into a FROM/WHERE half so the count
# and the rows are the same question asked twice and cannot drift apart: a
# filter added to one is a filter added to both.
#
# A dense radius is not a corner case here -- the measurement in COMPARISON
# below (a CABA branch with 621 neighbours within 5 km) is this table, and 10
# km is what MAX_RADIUS_KM allows. Before this was paged, every caller got all
# of them: the two pickers downloaded the whole set to show sixty rows and
# filtered the rest away in the browser.
COMPETITORS_FROM = """
    FROM branch_neighbor n
    JOIN branch b ON b.company_id = n.other_company_id AND b.banner_id = n.other_banner_id
                 AND b.branch_id = n.other_branch_id
    WHERE n.company_id = %(company_id)s AND n.banner_id = %(banner_id)s
      AND n.branch_id = %(branch_id)s AND n.distance_m <= %(radius_m)s
      AND (%(q)s::text IS NULL OR b.name ILIKE %(q)s
           OR b.banner_name ILIKE %(q)s OR b.locality ILIKE %(q)s)
"""

COMPETITORS = f"""
    SELECT n.other_company_id AS company_id, n.other_banner_id AS banner_id,
           n.other_branch_id AS branch_id, n.distance_m,
           b.banner_name, b.name, b.locality, b.province, b.latitude, b.longitude
    {COMPETITORS_FROM}
    -- distance_m is a rounded metre and two chains in one shopping centre tie
    -- on it, so it is not a total order on its own. The branch key breaks the
    -- tie and keeps a row from repeating on one page and vanishing from the
    -- next -- same reason search_products orders by `ean` after similarity.
    ORDER BY n.distance_m, n.other_company_id, n.other_banner_id, n.other_branch_id
    LIMIT %(limit)s OFFSET %(offset)s
"""

COMPETITORS_COUNT = f"SELECT count(*) AS n {COMPETITORS_FROM}"


def competitors(connection: Any, company_id: int, banner_id: int, branch_id: str,
                radius_km: float, q: str | None, limit: int,
                offset: int) -> dict[str, Any]:
    """A page of neighbours plus how many the radius holds in all.

    The total is what lets a screen say "12 de 621" rather than "12", and what
    the radius control reports -- a count that shrank because the page did
    would make the radius slider look broken.
    """
    params = {
        "company_id": company_id, "banner_id": banner_id, "branch_id": branch_id,
        "radius_m": radius_km * 1000, "q": f"%{q}%" if q else None,
        "limit": limit, "offset": offset,
    }
    total = connection.execute(COMPETITORS_COUNT, params).fetchone()["n"]
    return {"total": total, "items": connection.execute(COMPETITORS, params).fetchall()}


def branch_changes(connection: Any, company_id: int, banner_id: int, branch_id: str,
                   feed_date: str, min_pct: float, limit: int) -> list[dict[str, Any]]:
    return connection.execute("""
        SELECT c.ean, pr.description, c.previous_price, c.price, c.delta_pct
        FROM price_change c LEFT JOIN product pr ON pr.ean = c.ean
        WHERE c.feed_date = %s AND c.company_id = %s AND c.banner_id = %s
          AND c.branch_id = %s AND abs(c.delta_pct) >= %s
        ORDER BY abs(c.delta_pct) DESC LIMIT %s
    """, (feed_date, company_id, banner_id, branch_id, min_pct, limit)).fetchall()


# -- Alerts and watch rules -----------------------------------------------------

def list_alerts(connection: Any, org_id: int, unread_only: bool,
                limit: int, offset: int) -> list[dict[str, Any]]:
    clause = "AND a.read_at IS NULL" if unread_only else ""
    return connection.execute(f"""
        SELECT a.alert_id, a.rule_id, r.name AS rule_name, a.feed_date, a.ean,
               pr.description, a.company_id, a.banner_id, a.branch_id,
               b.banner_name, b.name AS branch_name, a.previous_price, a.price,
               a.delta_pct, a.created_at, a.read_at
        FROM alert a
        JOIN watch_rule r ON r.rule_id = a.rule_id
        LEFT JOIN product pr ON pr.ean = a.ean
        LEFT JOIN branch b ON b.company_id = a.company_id AND b.banner_id = a.banner_id
                          AND b.branch_id = a.branch_id
        WHERE a.org_id = %s {clause}
        ORDER BY a.created_at DESC LIMIT %s OFFSET %s
    """, (org_id, limit, offset)).fetchall()


def unread_alert_count(connection: Any, org_id: int) -> int:
    return connection.execute(
        "SELECT count(*) AS n FROM alert WHERE org_id = %s AND read_at IS NULL",
        (org_id,)).fetchone()["n"]


def mark_alert_read(connection: Any, org_id: int, alert_id: int) -> bool:
    row = connection.execute(
        "UPDATE alert SET read_at = now() WHERE alert_id = %s AND org_id = %s "
        "AND read_at IS NULL RETURNING alert_id", (alert_id, org_id)).fetchone()
    return row is not None


def mark_all_alerts_read(connection: Any, org_id: int) -> int:
    rows = connection.execute(
        "UPDATE alert SET read_at = now() WHERE org_id = %s AND read_at IS NULL "
        "RETURNING alert_id", (org_id,)).fetchall()
    return len(rows)


# The three target_* columns are SEPA identifiers, and a rule listed by them
# reads as "la cadena #15, sucursal 344" -- which says nothing to the merchant
# who picked "Dia Rivadavia 1234" from a list. The lateral resolves them back
# into the names that were on screen when the rule was created.
#
# It matches on whichever of the three the rule actually fixed: a rule that
# names only the chain has no single branch to name, so only the banner comes
# back and the branch columns stay NULL. LIMIT 1 is what makes that legal --
# any branch of the chain carries the same banner_name.
#
# Cheap in every case: the three columns are `branch`'s primary key in that
# order, so a fully-specified target is one index probe and a chain-only one is
# a prefix seek, and the ON clause skips the lateral entirely for the common
# rule that names no target at all.
LIST_RULES = """
    SELECT r.rule_id, r.name, r.company_id, r.banner_id, r.branch_id, r.radius_km,
           r.target_company_id, r.target_banner_id, r.target_branch_id,
           r.threshold_pct, r.enabled, r.created_at,
           t.banner_name AS target_banner_name,
           CASE WHEN r.target_branch_id IS NOT NULL THEN t.name END AS target_branch_name,
           CASE WHEN r.target_branch_id IS NOT NULL THEN t.locality END AS target_locality
    FROM watch_rule r
    LEFT JOIN LATERAL (
        SELECT b.banner_name, b.name, b.locality
        FROM branch b
        WHERE b.company_id = r.target_company_id
          AND (r.target_banner_id IS NULL OR b.banner_id = r.target_banner_id)
          AND (r.target_branch_id IS NULL OR b.branch_id = r.target_branch_id)
        LIMIT 1
    ) t ON r.target_company_id IS NOT NULL
    WHERE r.org_id = %s
    ORDER BY r.created_at
"""


def list_rules(connection: Any, org_id: int) -> list[dict[str, Any]]:
    rules = connection.execute(LIST_RULES, (org_id,)).fetchall()
    for rule in rules:
        rule["products"] = [row["ean"] for row in connection.execute(
            "SELECT ean FROM watch_product WHERE rule_id = %s", (rule["rule_id"],)).fetchall()]
    return rules


def create_rule(connection: Any, org_id: int, name: str, company_id: int, banner_id: int,
                branch_id: str, radius_km: float | None, target_company_id: int | None,
                target_banner_id: int | None, target_branch_id: str | None,
                threshold_pct: float, products: list[str]) -> int:
    row = connection.execute("""
        INSERT INTO watch_rule (org_id, name, company_id, banner_id, branch_id, radius_km,
                                target_company_id, target_banner_id, target_branch_id,
                                threshold_pct)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING rule_id
    """, (org_id, name, company_id, banner_id, branch_id, radius_km, target_company_id,
         target_banner_id, target_branch_id, threshold_pct)).fetchone()
    rule_id = row["rule_id"]
    for ean in products:
        connection.execute(
            "INSERT INTO watch_product (rule_id, ean) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (rule_id, ean))
    return rule_id


def set_rule_enabled(connection: Any, org_id: int, rule_id: int, enabled: bool) -> bool:
    row = connection.execute(
        "UPDATE watch_rule SET enabled = %s WHERE rule_id = %s AND org_id = %s "
        "RETURNING rule_id", (enabled, rule_id, org_id)).fetchone()
    return row is not None


def delete_rule(connection: Any, org_id: int, rule_id: int) -> bool:
    row = connection.execute(
        "DELETE FROM watch_rule WHERE rule_id = %s AND org_id = %s RETURNING rule_id",
        (rule_id, org_id)).fetchone()
    return row is not None


def replace_rule_products(connection: Any, org_id: int, rule_id: int,
                          products: list[str]) -> bool:
    owned = connection.execute(
        "SELECT 1 FROM watch_rule WHERE rule_id = %s AND org_id = %s",
        (rule_id, org_id)).fetchone()
    if owned is None:
        return False
    connection.execute("DELETE FROM watch_product WHERE rule_id = %s", (rule_id,))
    for ean in products:
        connection.execute(
            "INSERT INTO watch_product (rule_id, ean) VALUES (%s, %s)", (rule_id, ean))
    return True


# -- Uploads ----------------------------------------------------------------

def get_merchant(connection: Any, org_id: int) -> dict[str, Any] | None:
    """La sucursal propia de la org, con la ubicación que tiene en `branch`.

    El LEFT JOIN y no un JOIN: `merchant_source` es la fila que manda, y una
    sin su `branch` es una instalación a medio arreglar, no una org sin
    sucursal. Devolver la fila igual deja que el portal muestre el formulario
    con lo que hay en lugar de decir que no existe nada.
    """
    return connection.execute("""
        SELECT m.company_id, m.banner_id, m.branch_id, m.name,
               b.province, b.locality, b.latitude, b.longitude
        FROM merchant_source m
        LEFT JOIN branch b USING (company_id, banner_id, branch_id)
        WHERE m.org_id = %s
    """, (org_id,)).fetchone()


def upsert_merchant(connection: Any, org_id: int, name: str, province: str,
                    locality: str, latitude: float, longitude: float) -> dict[str, Any]:
    """Sets up (or edits) the merchant's single branch: the org's own row in
    `merchant_source`, its `branch` row (which is what makes it show up on the
    competitive map), and an org_branch subscription to itself."""
    merchant = connection.execute("""
        INSERT INTO merchant_source (org_id, name) VALUES (%s, %s)
        ON CONFLICT (org_id) DO UPDATE SET name = EXCLUDED.name
        RETURNING company_id, banner_id, branch_id, name
    """, (org_id, name)).fetchone()
    connection.execute("""
        INSERT INTO branch (company_id, banner_id, branch_id, banner_name, legal_name,
                            name, province, locality, latitude, longitude)
        VALUES (%(company_id)s, %(banner_id)s, %(branch_id)s, %(name)s, %(name)s,
                %(name)s, %(province)s, %(locality)s, %(latitude)s, %(longitude)s)
        ON CONFLICT (company_id, banner_id, branch_id) DO UPDATE SET
            banner_name = EXCLUDED.banner_name, name = EXCLUDED.name,
            province = EXCLUDED.province, locality = EXCLUDED.locality,
            latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude
    """, {**merchant, "province": province, "locality": locality,
         "latitude": latitude, "longitude": longitude})
    connection.execute("""
        INSERT INTO org_branch (org_id, company_id, banner_id, branch_id, label)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (org_id, company_id, banner_id, branch_id) DO NOTHING
    """, (org_id, merchant["company_id"], merchant["banner_id"], merchant["branch_id"], name))
    return merchant


def create_upload(connection: Any, upload_id: str, org_id: int, company_id: int,
                  feed_date: str, s3_key: str, filename: str, user_id: int) -> None:
    # upload_id is generated by the caller, not by the table's own default:
    # the backend needs it to build the S3 key *before* this row exists.
    connection.execute("""
        INSERT INTO price_upload (upload_id, org_id, company_id, feed_date, s3_key,
                                  filename, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (upload_id, org_id, company_id, feed_date, s3_key, filename, user_id))


def get_upload(connection: Any, org_id: int, upload_id: str) -> dict[str, Any] | None:
    return connection.execute("""
        SELECT u.upload_id, u.feed_date, u.s3_key, u.filename, u.status, u.job_id,
               u.created_at, j.status AS job_status, j.rows_ok, j.rows_rejected,
               j.last_error, j.chain_id
        FROM price_upload u LEFT JOIN ingest_job j ON j.job_id = u.job_id
        WHERE u.upload_id = %s AND u.org_id = %s
    """, (upload_id, org_id)).fetchone()


# What delete_upload answers for an upload a worker is loading (`queued`).
UPLOAD_IN_PROGRESS = "in_progress"


def delete_upload(connection: Any, org_id: int,
                  upload_id: str) -> dict[str, Any] | str | None:
    """Borra una carga y deshace lo que dejó. `None` si no es de esta org.

    Borrar sólo la fila de `price_upload` sería peor que no ofrecer el botón:
    los precios de esa lista siguen en `price`, así que la sucursal sigue
    apareciendo en la comparación con datos de un archivo que el usuario cree
    que borró, y no queda forma de sacarlos desde el portal.

    Los precios se borran por `(feed_date, company_id)`, que es el mismo
    alcance que usa el worker al reprocesar (app/batch/worker/main.py) -- una carga es
    la lista completa de ese día para esa sucursal, no un delta. De ahí la
    guarda: si hay **otra** carga viva para el mismo día, las filas de `price`
    no son atribuibles sólo a ésta, así que se borra la carga y el archivo
    pero los precios quedan. Solo cuenta una carga que terminó en `done`: una
    que falló, o que todavía no se procesó, no dejó precios. Devuelve la
    cuenta de lo borrado para que la respuesta pueda decir qué pasó en lugar
    de un OK a ciegas.
    """
    row = connection.execute("""
        SELECT upload_id, company_id, feed_date, s3_key, filename, job_id, status
        FROM price_upload WHERE upload_id = %s AND org_id = %s
    """, (upload_id, org_id)).fetchone()
    if row is None:
        return None
    if row["status"] == "queued":
        return UPLOAD_IN_PROGRESS

    otras = connection.execute("""
        SELECT count(*) AS n FROM price_upload
        WHERE org_id = %s AND company_id = %s AND feed_date = %s AND upload_id <> %s
          AND status = 'done'
    """, (org_id, row["company_id"], row["feed_date"], upload_id)).fetchone()["n"]

    borrado = {**row, "otras_cargas_del_dia": otras,
               "alertas": 0, "cambios": 0, "precios": 0}
    if otras == 0:
        # En este orden por las dependencias: una alerta cuelga de un cambio,
        # y un cambio de un precio.
        clave = (row["feed_date"], row["company_id"])
        borrado["alertas"] = connection.execute(
            "DELETE FROM alert WHERE feed_date = %s AND company_id = %s", clave).rowcount
        borrado["cambios"] = connection.execute(
            "DELETE FROM price_change WHERE feed_date = %s AND company_id = %s", clave).rowcount
        borrado["precios"] = connection.execute(
            "DELETE FROM price WHERE feed_date = %s AND company_id = %s", clave).rowcount

    connection.execute("DELETE FROM price_upload WHERE upload_id = %s", (upload_id,))
    # Después de la carga, no antes: `price_upload.job_id` referencia a
    # `ingest_job` y sólo se pone en NULL al borrar el job, no se cascadea.
    if row["job_id"] is not None:
        connection.execute("DELETE FROM ingest_job WHERE job_id = %s", (row["job_id"],))
    return borrado


def list_uploads(connection: Any, org_id: int, limit: int) -> list[dict[str, Any]]:
    return connection.execute("""
        SELECT u.upload_id, u.feed_date, u.filename, u.status, u.job_id, u.created_at,
               j.status AS job_status, j.rows_ok, j.rows_rejected, j.last_error
        FROM price_upload u LEFT JOIN ingest_job j ON j.job_id = u.job_id
        WHERE u.org_id = %s ORDER BY u.created_at DESC LIMIT %s
    """, (org_id, limit)).fetchall()


# -- Auth ---------------------------------------------------------------------

def create_org(connection: Any, name: str, slug: str) -> int:
    return connection.execute(
        "INSERT INTO org (name, slug) VALUES (%s, %s) RETURNING org_id",
        (name, slug)).fetchone()["org_id"]


# The user, as Cognito knows it: `sub` is the key; the email is unique too.
# A known sub keeps its row and takes the token's email. An unknown one gets
# a new row, or takes the row with its email: Cognito confirms the email
# before the first login, so the same email after a re-registration (a new
# sub) is the same person.
LOGIN_USER = """
    WITH known AS (
      UPDATE app_user SET email = %(email)s, last_login_at = now()
      WHERE cognito_sub = %(sub)s
      RETURNING user_id
    ), first_login AS (
      INSERT INTO app_user (cognito_sub, email, last_login_at)
      SELECT %(sub)s, %(email)s, now() WHERE NOT EXISTS (SELECT 1 FROM known)
      ON CONFLICT (email) DO UPDATE
        SET cognito_sub = EXCLUDED.cognito_sub, last_login_at = now()
      RETURNING user_id
    )
    SELECT user_id FROM known UNION ALL SELECT user_id FROM first_login
"""


def login_user(connection: Any, sub: str, email: str) -> dict[str, Any]:
    """The user behind a token, created on its first login with no org."""
    user_id = connection.execute(
        LOGIN_USER, {"sub": sub, "email": email.lower()}).fetchone()["user_id"]
    return get_user(connection, user_id)


def get_user_by_sub(connection: Any, sub: str) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT user_id, org_id, role FROM app_user WHERE cognito_sub = %s",
        (sub,)).fetchone()


def join_org(connection: Any, user_id: int, org_id: int, role: str) -> bool:
    """Into an org, if the user has none. False if it already had one."""
    return connection.execute(
        "UPDATE app_user SET org_id = %s, role = %s WHERE user_id = %s AND org_id IS NULL",
        (org_id, role, user_id)).rowcount == 1


def add_member(connection: Any, org_id: int, email: str) -> int | None:
    """An invited member's row in the org, as `member`: a new one, or the row
    of a user who never named an org. None if that email is already in an
    org, this one or another."""
    row = connection.execute("""
        INSERT INTO app_user (org_id, email, role) VALUES (%s, %s, 'member')
        ON CONFLICT (email) DO UPDATE SET org_id = EXCLUDED.org_id, role = 'member'
          WHERE app_user.org_id IS NULL
        RETURNING user_id
    """, (org_id, email)).fetchone()
    return row["user_id"] if row else None


def set_cognito_sub(connection: Any, user_id: int, sub: str) -> bool:
    """The row's Cognito user. False if that sub is another row's. In a
    savepoint, so that False leaves the caller's transaction usable."""
    try:
        with connection.transaction():
            connection.execute(
                "UPDATE app_user SET cognito_sub = %s WHERE user_id = %s", (sub, user_id))
    except psycopg.errors.UniqueViolation:
        return False
    return True


MEMBER_COLUMNS = "user_id, email, role, created_at, last_login_at"


def list_members(connection: Any, org_id: int) -> list[dict[str, Any]]:
    """The org's users, the owner first. `last_login_at` is null for an
    invited member who has not logged in yet."""
    return connection.execute(f"""
        SELECT {MEMBER_COLUMNS} FROM app_user
        WHERE org_id = %s ORDER BY role = 'owner' DESC, created_at, user_id
    """, (org_id,)).fetchall()


def get_member(connection: Any, org_id: int, user_id: int) -> dict[str, Any] | None:
    return connection.execute(
        f"SELECT {MEMBER_COLUMNS} FROM app_user WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchone()


def get_user(connection: Any, user_id: int) -> dict[str, Any] | None:
    """The user and its org: org_id, org_name and org_slug are null until it
    names one."""
    return connection.execute("""
        SELECT u.user_id, u.email, u.role, u.org_id, o.name AS org_name, o.slug AS org_slug
        FROM app_user u LEFT JOIN org o ON o.org_id = u.org_id WHERE u.user_id = %s
    """, (user_id,)).fetchone()


# The comparison above builds its rival set from `branch_neighbor` filtered by
# radius. This one takes the branches explicitly, which is the whole point: a
# merchant wants to look at the two shops they actually lose customers to, and
# those are not always the nearest ones. Bypassing `branch_neighbor` also means
# a rival outside the radius still compares.
HEAD_TO_HEAD = """
    WITH mine AS MATERIALIZED (
      SELECT ean, price FROM price
      WHERE feed_date = %(feed_date)s AND company_id = %(company_id)s
        AND banner_id = %(banner_id)s AND branch_id = %(branch_id)s
    ),
    targets AS (
      SELECT * FROM unnest(
        %(target_company_ids)s::int[],
        %(target_banner_ids)s::int[],
        %(target_branch_ids)s::text[]
      ) AS t(company_id, banner_id, branch_id)
    ),
    rival AS (
      SELECT p.ean,
             t.company_id || ':' || t.banner_id || ':' || t.branch_id AS rival_key,
             p.price
      FROM targets t
      JOIN price p ON p.feed_date = %(feed_date)s AND p.company_id = t.company_id
                   AND p.banner_id = t.banner_id AND p.branch_id = t.branch_id
    ),
    joined AS (
      SELECT m.ean, m.price AS own_price,
             jsonb_object_agg(r.rival_key, r.price) AS rival_prices,
             min(r.price) AS min_price,
             round(avg(r.price), 2) AS avg_price,
             count(*) AS offers
      FROM mine m
      JOIN rival r ON r.ean = m.ean
      GROUP BY m.ean, m.price
    ),
    scored AS (
      SELECT j.ean, pr.description, pr.brand, j.own_price, j.rival_prices,
             j.min_price, j.avg_price, j.offers,
             round(100 * (j.own_price - j.min_price) / nullif(j.min_price, 0), 2)
               AS pct_over_min
      FROM joined j
      LEFT JOIN product pr ON pr.ean = j.ean
      WHERE (%(q)s::text IS NULL OR pr.description ILIKE %(q)s)
    )
    SELECT ean, description, brand, own_price, rival_prices, min_price, avg_price,
           offers, pct_over_min
    FROM scored
    ORDER BY (min_price < %(price_floor)s
              OR min_price < avg_price / %(outlier_ratio)s
              OR pct_over_min > %(max_gap_pct)s) ASC,
             pct_over_min DESC NULLS LAST,
             ean
    LIMIT %(limit)s OFFSET %(offset)s
"""


def head_to_head(connection: Any, company_id: int, banner_id: int, branch_id: str,
                 feed_date: str, targets: list[tuple[int, int, str]], q: str | None,
                 limit: int, offset: int) -> list[dict[str, Any]]:
    return connection.execute(HEAD_TO_HEAD, {
        "company_id": company_id, "banner_id": banner_id, "branch_id": branch_id,
        "feed_date": feed_date,
        "target_company_ids": [t[0] for t in targets],
        "target_banner_ids": [t[1] for t in targets],
        "target_branch_ids": [t[2] for t in targets],
        "q": f"%{q}%" if q else None, "limit": limit, "offset": offset,
        "price_floor": IMPLAUSIBLE_PRICE_FLOOR, "outlier_ratio": OUTLIER_RATIO,
        "max_gap_pct": MAX_CREDIBLE_GAP_PCT,
    }).fetchall()


# One product's price at every competitor inside the radius. The product page
# used to show how each chain *names* this barcode, which is a curiosity; what
# a merchant standing in front of the shelf wants is what the shop down the
# street charges for it.
PRODUCT_COMPETITOR_PRICES = """
    SELECT n.other_company_id AS company_id, n.other_banner_id AS banner_id,
           n.other_branch_id AS branch_id, n.distance_m,
           b.banner_name, b.name, b.locality,
           p.price
    FROM branch_neighbor n
    JOIN branch b ON b.company_id = n.other_company_id AND b.banner_id = n.other_banner_id
                 AND b.branch_id = n.other_branch_id
    JOIN price p ON p.feed_date = %(feed_date)s AND p.company_id = n.other_company_id
                AND p.banner_id = n.other_banner_id AND p.branch_id = n.other_branch_id
                AND p.ean = %(ean)s
    WHERE n.company_id = %(company_id)s AND n.banner_id = %(banner_id)s
      AND n.branch_id = %(branch_id)s AND n.distance_m <= %(radius_m)s
    -- Cheapest first: the number that matters is the one to beat, and the
    -- nearest shop is only the tiebreaker.
    ORDER BY p.price ASC, n.distance_m ASC
    LIMIT %(limit)s
"""


def product_competitor_prices(connection: Any, company_id: int, banner_id: int,
                              branch_id: str, ean: str, feed_date: str,
                              radius_km: float, limit: int) -> list[dict[str, Any]]:
    return connection.execute(PRODUCT_COMPETITOR_PRICES, {
        "company_id": company_id, "banner_id": banner_id, "branch_id": branch_id,
        "ean": ean, "feed_date": feed_date, "radius_m": radius_km * 1000, "limit": limit,
    }).fetchall()
