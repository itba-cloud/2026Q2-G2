"""The login's callback: a Lambda behind the API's public GET /auth/callback.

Cognito's Hosted UI sends the browser here with a one-time `code` (and the
`state` the portal made up). This trades the code for the tokens at
Cognito's token endpoint, authenticating with the client's secret, and sends
the browser back to the portal with the ID token in the URL's fragment:

    302 <portal>#/auth/callback?id_token=...&expires_in=...&state=...

The secret lives only here, never in the browser. The fragment never leaves
the browser either: it is not part of the request to S3, so the token
reaches no server and no log. The refresh token is not passed on: the
portal logs in again when the ID token expires.

Anything that goes wrong (Cognito's own `error`, a missing or used code)
sends the browser to the portal's login with the reason:

    302 <portal>#/login?error=...

    handler(event, context) -> API Gateway response (payload 2.0)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger("auth_callback")
log.setLevel(logging.INFO)

COGNITO_URL = os.environ["COGNITO_URL"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REDIRECT_URI = os.environ["REDIRECT_URI"]
PORTAL_URL = os.environ["PORTAL_URL"]

# Well under the function's own timeout.
TOKEN_TIMEOUT_SECONDS = 5


class LoginFailed(Exception):
    """Why the browser goes back to the login, in words the portal shows."""


def redirect(route: str, params: dict[str, str]) -> dict[str, Any]:
    """Back to a HashRouter route of the portal, with `params` in its query."""
    location = f"{PORTAL_URL}#{route}?{urllib.parse.urlencode(params)}"
    return {
        "statusCode": 302,
        "headers": {
            "Location": location,
            # The tokens are in the Location: nothing may keep it.
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
        "body": "",
    }


def exchange(code: str) -> dict[str, Any]:
    """The code for the tokens, at Cognito's token endpoint."""
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    request = urllib.request.Request(
        f"{COGNITO_URL}/oauth2/token", method="POST",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "code": code, "redirect_uri": REDIRECT_URI}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Authorization": f"Basic {credentials}"})
    try:
        with urllib.request.urlopen(request, timeout=TOKEN_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        # invalid_grant: a code that expired, was already used or never existed.
        reason = json.loads(error.read() or b"{}").get("error", f"HTTP {error.code}")
        log.warning("token endpoint refused the code: %s", reason)
        raise LoginFailed(reason) from error
    except (urllib.error.URLError, TimeoutError) as error:
        log.error("token endpoint unreachable: %s", error)
        raise LoginFailed("cognito_unreachable") from error


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    state = query.get("state", "")
    try:
        if "error" in query:
            # The user cancelled, or Cognito refused the request.
            raise LoginFailed(query.get("error_description") or query["error"])
        if not query.get("code"):
            raise LoginFailed("missing_code")
        tokens = exchange(query["code"])
    except LoginFailed as failure:
        return redirect("/login", {"error": str(failure)})
    log.info("login completed")
    return redirect("/auth/callback", {
        "id_token": tokens["id_token"],
        "expires_in": str(tokens.get("expires_in", "")),
        "state": state,
    })
