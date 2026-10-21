"""The portal, the Cognito login and the API's gate (tickets 05 and 06).

The portal is served over HTTPS from its bucket with the configuration
Terraform generated; a merchant signs up or logs in on Cognito's Hosted UI
and the callback hands the portal its tokens; every API route but /health
asks for a valid JWT and the API knows the user by its `sub`; the first
login asks for the organization's name and leaves the user as its owner; and
the own login is gone.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
import uuid

from cognito import (PLATFORM_ADMIN, authorize_query, fragment_params, id_token, new_user,
                     random_email, random_password, secret_hash, sign_in)
from harness import (GREY, REGION, RESET, SCREEN_READS, Checks, NoRedirect, Stack, api, fetch,
                     statuses)

def check_portal(checks: Checks, stack: Stack) -> None:
    checks.section("Portal · served over HTTPS from its bucket")
    portal = stack.outputs["portal_url"]
    checks.expect("its URL is HTTPS", portal.startswith("https://"), portal)
    page = fetch(portal)
    checks.expect("GET of the portal is 200 with the app's page",
                  page.status == 200 and 'id="root"' in page.body,
                  f"{page.status} {page.body[:200]}")
    checks.expect("served as HTML", page.headers.get("content-type", "").startswith("text/html"),
                  page.headers.get("content-type", ""))

    config = fetch(urllib.parse.urljoin(portal, "config.json"))
    checks.expect("config.json is there", config.status == 200, f"{config.status}")
    if config.status != 200:
        return
    values = config.json()
    expected = {"apiUrl": stack.api, "authUrl": stack.outputs["cognito_hosted_ui_url"],
                "clientId": stack.client_id, "redirectUri": stack.outputs["auth_callback_url"],
                "logoutUri": portal}
    checks.expect("with the URLs and the client Terraform created", values == expected,
                  f"got {values}, expected {expected}")
    checks.expect("and without the client secret", stack.client_secret not in config.body)

    bucket = stack.outputs["portal_bucket_name"]
    versioning = stack.s3.get_bucket_versioning(Bucket=bucket)
    checks.expect("the portal's bucket is not versioned", "Status" not in versioning,
                  str(versioning.get("Status")))
    checks.expect("Terraform's outputs name the same portal", bucket in portal)


def check_hosted_ui(checks: Checks, stack: Stack) -> None:
    checks.section("Cognito · Hosted UI with open registration")
    hosted_ui = stack.outputs["cognito_hosted_ui_url"]
    login = fetch(f"{hosted_ui}/oauth2/authorize?{authorize_query(stack, 'x')}")
    checks.expect("the authorize URL the portal builds opens the login page",
                  login.status == 200 and "cognitoSignInForm" in login.body,
                  f"{login.status} {login.body[:200]}")
    signup = fetch(f"{hosted_ui}/signup?{authorize_query(stack, 'x')}")
    checks.expect("and the sign-up page", signup.status == 200, f"{signup.status}")

    # The call the Hosted UI's sign-up form makes: anyone may register.
    email = random_email()
    try:
        stack.cognito.sign_up(ClientId=stack.client_id, SecretHash=secret_hash(stack, email),
                              Username=email, Password=random_password(),
                              UserAttributes=[{"Name": "email", "Value": email}])
        stack.users.append(email)
        signed_up = True
    except stack.cognito.exceptions.ClientError as error:
        signed_up = False
        print(f"{GREY}      {error}{RESET}")
    checks.expect("anyone can sign up, with no admin in between", signed_up)

    group = stack.cognito.get_group(UserPoolId=stack.pool_id, GroupName=PLATFORM_ADMIN)
    checks.expect("the platform-admin group exists", group["Group"]["GroupName"] == PLATFORM_ADMIN)


def check_callback(checks: Checks, stack: Stack) -> None:
    checks.section("Callback · trades the code for the tokens, secret kept server-side")
    portal, callback = stack.outputs["portal_url"], stack.outputs["auth_callback_url"]
    no_redirect = urllib.request.build_opener(NoRedirect)

    missing = fetch(callback, opener=no_redirect)
    location = missing.headers.get("location", "")
    checks.expect("without a code it sends the browser back to the portal's login",
                  missing.status == 302 and location.startswith(portal)
                  and "#/login" in location, f"{missing.status} {location}")

    bogus = fetch(f"{callback}?code=not-a-code&state=s", opener=no_redirect)
    location = bogus.headers.get("location", "")
    checks.expect("a code Cognito never issued gets no token",
                  bogus.status == 302 and "#/login" in location and "id_token" not in location,
                  f"{bogus.status} {location}")

    # A real login, the way a browser does it: the Hosted UI's form, then
    # Cognito's redirect to the callback, then the callback's to the portal.
    email, password = new_user(stack)
    browser, posted = sign_in(stack, email, password)
    if posted is None:
        checks.expect("the Hosted UI's login form logs in", False, "no form")
        return
    to_callback = posted.headers.get("location", "")
    checks.expect("the Hosted UI's login form sends the browser to the callback with a code",
                  posted.status == 302 and to_callback.startswith(callback)
                  and "code=" in to_callback, f"{posted.status} {to_callback[:200]}")
    if not to_callback.startswith(callback):
        return

    landed = fetch(to_callback, opener=browser.opener)
    to_portal = landed.headers.get("location", "")
    params = fragment_params(to_portal)
    checks.expect("the callback sends it to the portal with the ID token in the fragment",
                  landed.status == 302 and to_portal.startswith(portal)
                  and bool(params.get("id_token")), f"{landed.status} {to_portal[:120]}")
    checks.expect("and with the state the portal sent", params.get("state") == browser.state)
    checks.expect("never in the query, which would reach a server",
                  "id_token" not in urllib.parse.urlsplit(to_portal).query)
    checks.expect("and without the client secret", stack.client_secret not in to_portal)
    if params.get("id_token"):
        me = api(stack, "GET", "/auth/me", params["id_token"])
        checks.expect("the API takes that token", me.status == 200 and
                      (me.json() or {}).get("email") == email, f"{me.status} {me.body[:200]}")


def check_jwt(checks: Checks, stack: Stack) -> None:
    checks.section("API · every route but /health asks for a valid JWT")
    health = api(stack, "GET", "/health")
    checks.expect("GET /api/health answers without a token", health.status == 200,
                  f"{health.status}")

    # What the browser asks first, with no token: API Gateway answers it.
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(stack.outputs["portal_url"]))
    preflight = fetch(f"{stack.api}/api/me/branches", "OPTIONS", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    checks.expect("the portal's CORS preflight answers without a token",
                  preflight.status in (200, 204)
                  and preflight.headers.get("access-control-allow-origin") in ("*", origin),
                  f"{preflight.status} {preflight.headers}")

    anonymous = statuses(stack, None, SCREEN_READS)
    checks.expect(f"none of the {len(SCREEN_READS)} reads answers without one",
                  all(status == 401 for status in anonymous.values()), str(anonymous))

    # Well formed and signed, but not by this user pool.
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","kid":"x"}').rstrip(b"=").decode()
    claims = base64.urlsafe_b64encode(json.dumps({
        "sub": str(uuid.uuid4()), "aud": stack.client_id, "token_use": "id",
        "iss": f"https://cognito-idp.{REGION}.amazonaws.com/{stack.pool_id}",
        "exp": 4102444800}).encode()).rstrip(b"=").decode()
    forged = f"{header}.{claims}.{base64.urlsafe_b64encode(b'sig').rstrip(b'=').decode()}"
    checks.expect("a forged token is 401", api(stack, "GET", "/auth/me", forged).status == 401)

    token = id_token(stack, *new_user(stack))
    for path, body in (("/auth/register", {"org_name": "x", "email": "a@b.c", "password": "x" * 8}),
                       ("/auth/login", {"email": "a@b.c", "password": "x" * 8})):
        gone = api(stack, "POST", path, token, body)
        checks.expect(f"POST /api{path} is gone", gone.status in (404, 405), f"{gone.status}")


def check_onboarding(checks: Checks, stack: Stack) -> None:
    checks.section("Onboarding · the first login asks for the organization")
    email, password = new_user(stack)
    token = id_token(stack, email, password)

    first = api(stack, "GET", "/auth/me", token)
    me = first.json() or {}
    checks.expect("the first GET /auth/me creates the user, by its sub, with no org",
                  first.status == 200 and me.get("email") == email and me.get("org_id") is None,
                  f"{first.status} {first.body[:200]}")
    again = api(stack, "GET", "/auth/me", token).json() or {}
    checks.expect("a second one finds the same user", again.get("user_id") == me.get("user_id"))

    no_org = api(stack, "GET", "/me/branches", token)
    checks.expect("with no org the org's data is 403", no_org.status == 403, f"{no_org.status}")

    created = api(stack, "POST", "/me/org", token, {"name": "LGDC verify"})
    user = created.json() or {}
    checks.expect("naming the org creates it and leaves the user as its owner",
                  created.status == 201 and user.get("role") == "owner"
                  and user.get("org_name") == "LGDC verify" and user.get("org_id") is not None,
                  f"{created.status} {created.body[:200]}")
    twice = api(stack, "POST", "/me/org", token, {"name": "Otra"})
    checks.expect("a second org for the same user is 409", twice.status == 409, f"{twice.status}")
    after = api(stack, "GET", "/auth/me", token).json() or {}
    checks.expect("GET /auth/me now says owner of that org",
                  after.get("org_id") == user.get("org_id") and after.get("role") == "owner")


def run(checks: Checks, stack: Stack) -> None:
    check_portal(checks, stack)
    check_hosted_ui(checks, stack)
    check_callback(checks, stack)
    check_jwt(checks, stack)
    check_onboarding(checks, stack)
