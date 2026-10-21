"""The test users, and the Hosted UI the way a browser uses it.

Test users are created with Cognito's admin API, the way the platform admin
is, logged in without a browser (ADMIN_USER_PASSWORD_AUTH, which needs AWS
credentials) and deleted at the end of the run. Their rows in Postgres stay:
nothing outside the VPC can delete them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html.parser
import http.cookiejar
import secrets
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

from harness import NoRedirect, Response, Stack, api, fetch

PLATFORM_ADMIN = "platform-admin"


def random_email() -> str:
    return f"verify-{uuid.uuid4().hex[:10]}@lgdc.test"


def random_password() -> str:
    return f"Verify-{secrets.token_urlsafe(12)}1a"


def invited_email() -> str:
    """An address an invitation can really be sent to: SES's mailbox
    simulator takes it and drops it, so no email bounces."""
    return f"success+verify-{uuid.uuid4().hex[:10]}@simulator.amazonses.com"


def secret_hash(stack: Stack, username: str) -> str:
    """What Cognito asks of a client with a secret, on every call that names a user."""
    digest = hmac.new(stack.client_secret.encode(), (username + stack.client_id).encode(),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def new_user(stack: Stack) -> tuple[str, str]:
    """A confirmed user with a permanent password: (email, password)."""
    email, password = random_email(), random_password()
    stack.cognito.admin_create_user(
        UserPoolId=stack.pool_id, Username=email, MessageAction="SUPPRESS",
        UserAttributes=[{"Name": "email", "Value": email},
                        {"Name": "email_verified", "Value": "true"}])
    stack.users.append(email)
    stack.cognito.admin_set_user_password(
        UserPoolId=stack.pool_id, Username=email, Password=password, Permanent=True)
    return email, password


def id_token(stack: Stack, email: str, password: str) -> str:
    """The token the portal sends, without going through the browser."""
    result = stack.cognito.admin_initiate_auth(
        UserPoolId=stack.pool_id, ClientId=stack.client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password,
                        "SECRET_HASH": secret_hash(stack, email)})
    return result["AuthenticationResult"]["IdToken"]


def owner_of_new_org(stack: Stack, name: str) -> str:
    """A user who has already been through the onboarding: its token. Raises
    if the onboarding failed, which fails the suite that asked for it."""
    token = id_token(stack, *new_user(stack))
    me = api(stack, "GET", "/auth/me", token)
    created = api(stack, "POST", "/me/org", token, {"name": name})
    if me.status != 200 or created.status != 201:
        raise RuntimeError(f"onboarding failed: GET /auth/me {me.status}, "
                           f"POST /me/org {created.status} {created.body[:200]}")
    return token


def platform_admin(stack: Stack) -> str:
    """A user in the platform-admin group, with no org: its token."""
    email, password = new_user(stack)
    stack.cognito.admin_add_user_to_group(
        UserPoolId=stack.pool_id, Username=email, GroupName=PLATFORM_ADMIN)
    return id_token(stack, email, password)


# -- The Hosted UI, as a browser ----------------------------------------------

def authorize_query(stack: Stack, state: str) -> str:
    return urllib.parse.urlencode({
        "response_type": "code", "client_id": stack.client_id,
        "redirect_uri": stack.outputs["auth_callback_url"],
        "scope": "openid email profile", "state": state})


class HostedUiForm(html.parser.HTMLParser):
    """A form of the Hosted UI, by its name: where it posts, and its CSRF
    token. `cognitoSignInForm` logs in; `changepasswordform` is where a
    temporary password is traded for a new one."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.action: str | None = None
        self.csrf: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and self.action is None and attributes.get("name") == self.name:
            self.action = attributes.get("action")
        if tag == "input" and attributes.get("name") == "_csrf" and self.csrf is None:
            self.csrf = attributes.get("value")


@dataclass
class Browser:
    """A browser on the Hosted UI: its cookies, and the state it sent."""
    opener: urllib.request.OpenerDirector
    state: str


def post_form(browser: Browser, url: str, page: Response, name: str,
              fields: dict[str, str]) -> Response | None:
    """Posts the form `name` of `page` (fetched from `url`), with its CSRF
    token. None if the page has no such form."""
    form = HostedUiForm(name)
    form.feed(page.body)
    if page.status != 200 or not form.csrf:
        return None
    return fetch(urllib.parse.urljoin(url, form.action or url), "POST",
                 urllib.parse.urlencode({"_csrf": form.csrf, **fields}).encode(),
                 {"Content-Type": "application/x-www-form-urlencoded"}, browser.opener)


def sign_in(stack: Stack, email: str, password: str) -> tuple[Browser, Response | None]:
    """The Hosted UI's login form, the way a browser posts it: Cognito's
    answer, a redirect (None if there was no form)."""
    browser = Browser(urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect),
        secrets.token_urlsafe(16))
    login_url = (f"{stack.outputs['cognito_hosted_ui_url']}/login?"
                 f"{authorize_query(stack, browser.state)}")
    page = fetch(login_url, opener=browser.opener)
    return browser, post_form(browser, login_url, page, "cognitoSignInForm", {
        "username": email, "password": password, "signInSubmitButton": "Sign in"})


def fragment_params(location: str) -> dict[str, str]:
    """The query inside a HashRouter fragment: `<portal>#/auth/callback?a=1`."""
    fragment = urllib.parse.urlsplit(location).fragment
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(fragment).query))
