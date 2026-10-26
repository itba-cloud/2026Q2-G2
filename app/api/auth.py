"""Who is asking: the Cognito user behind the request, and its org.

API Gateway's JWT authorizer validates the token (signature, issuer,
audience, expiry) before this process ever runs, and hands the claims over
in the event: Mangum keeps it in the ASGI scope as `aws.event`. So nothing
here checks a signature. A request with no claims never came through API
Gateway's authorizer (the public routes, or a local run) and is refused.

The user is its token's `sub`. Its org and its role in it (owner, member)
live in Postgres; a user who has not named an org yet is an `Identity`
without a `CurrentUser`. The platform admin is above the orgs: it is the
Cognito group `platform-admin`, which travels in the token, so it needs no
org.

    from auth import (CurrentUser, Identity, current_identity, current_user,
                      require_owner, require_platform_admin)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status

import db
import queries


PLATFORM_ADMIN_GROUP = "platform-admin"


@dataclass(frozen=True)
class Identity:
    """The Cognito user, from its token's claims."""
    sub: str
    email: str
    groups: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CurrentUser:
    """A user with an org: what every tenant endpoint works with."""
    user_id: int
    org_id: int
    role: str


def _claims(request: Request) -> dict[str, Any]:
    event = request.scope.get("aws.event") or {}
    authorizer = event.get("requestContext", {}).get("authorizer") or {}
    return (authorizer.get("jwt") or {}).get("claims") or {}


def _groups(claims: dict[str, Any]) -> frozenset[str]:
    """`cognito:groups`. API Gateway hands a list claim over flattened into
    one string, `[platform-admin other]`; a list is taken as well."""
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = groups.strip("[]").replace(",", " ").split()
    return frozenset(groups)


def current_identity(request: Request) -> Identity:
    """The ID token's user. An access token from the same client also gets
    past the authorizer, but carries no email: refused."""
    claims = _claims(request)
    if not claims.get("sub") or claims.get("token_use") != "id" or not claims.get("email"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid ID token")
    return Identity(sub=claims["sub"], email=claims["email"].lower(), groups=_groups(claims))


def current_user(identity: Identity = Depends(current_identity)) -> CurrentUser:
    """The user and its org. 403 until the user names its org: the portal
    asks for it on the first login (POST /me/org)."""
    with db.get_connection() as connection:
        user = queries.get_user_by_sub(connection, identity.sub)
    if user is None or user["org_id"] is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "organization required")
    return CurrentUser(user["user_id"], user["org_id"], user["role"])


def require_owner(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner role required")
    return user


def require_platform_admin(identity: Identity = Depends(current_identity)) -> Identity:
    """In the platform-admin group, with or without an org."""
    if PLATFORM_ADMIN_GROUP not in identity.groups:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform-admin group required")
    return identity
