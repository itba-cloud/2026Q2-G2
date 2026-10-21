"""The roles (ticket 06): the owner invites a member, who changes Cognito's
temporary password on its first login and lands in the owner's org; the
owner's actions are 403 for a member; /admin/* answers only to the
platform-admin group; and Deploy's bootstrap step creates that platform admin,
and can run again over it.

The invited members get SES mailbox simulator addresses, so their real
invitation emails bounce nowhere.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import urllib.parse

from cognito import (PLATFORM_ADMIN, id_token, invited_email, new_user, owner_of_new_org,
                     platform_admin, post_form, random_email, random_password, sign_in,
                     fragment_params)
from harness import ROOT, Checks, Stack, api, fetch, statuses

# What only the org's owner does. A member gets a 403 on each. The invitation
# goes to SES's simulator, in case a regression ever lets it through.
OWNER_ACTIONS = (
    ("GET", "/ingest/jobs", None),
    ("GET", "/org/members", None),
    ("POST", "/org/members", {"email": "success+verify-member@simulator.amazonses.com"}),
)

# Only normalize is called for real: the neighbors' rebuild starts with a
# TRUNCATE of branch_neighbor, and the checks leave the data as they found it.
ADMIN_ACTIONS = ("/admin/neighbors/refresh", "/admin/catalog/normalize")


def first_login(checks: Checks, stack: Stack, email: str) -> str | None:
    """The invited member's first login, on the Hosted UI like a browser:
    the temporary password, the new one Cognito asks for, and the callback
    back to the portal. Its ID token, or None if a step failed.

    The real temporary password goes by email; this sets a known one the same
    way (not permanent), so the user stays in FORCE_CHANGE_PASSWORD."""
    temporary = f"Temp-{secrets.token_urlsafe(12)}1a"
    stack.cognito.admin_set_user_password(
        UserPoolId=stack.pool_id, Username=email, Password=temporary, Permanent=False)
    browser, posted = sign_in(stack, email, temporary)
    to_change = (posted.headers.get("location", "") if posted else "")
    change_url = urllib.parse.urljoin(stack.outputs["cognito_hosted_ui_url"], to_change)
    page = fetch(change_url, opener=browser.opener)
    asked = "/changePassword" in to_change and 'name="changepasswordform"' in page.body
    checks.expect("the member's first login asks for a new password", asked,
                  f"{posted.status if posted else 'no form'} {to_change[:120]}")
    if not asked:
        return None

    new_password = random_password()
    changed = post_form(browser, change_url, page, "changepasswordform",
                        {"password": new_password, "confirmPassword": new_password})
    to_callback = changed.headers.get("location", "") if changed else ""
    landed = fetch(to_callback, opener=browser.opener) if to_callback.startswith(
        stack.outputs["auth_callback_url"]) else None
    params = fragment_params(landed.headers.get("location", "")) if landed else {}
    checks.expect("with the new one it lands on the portal with its ID token",
                  bool(params.get("id_token")) and params.get("state") == browser.state,
                  f"{changed.status if changed else 'no form'} {to_callback[:120]}")
    return params.get("id_token")


def check_invitation(checks: Checks, stack: Stack) -> None:
    checks.section("Roles · the owner invites a member, who logs in with its own account")
    owner = owner_of_new_org(stack, "LGDC verify, roles")
    org_id = (api(stack, "GET", "/auth/me", owner).json() or {}).get("org_id")
    branches = api(stack, "GET", "/branches?limit=1", owner).json() or []
    branch = branches[0] if branches else None
    if branch is not None:
        api(stack, "POST", "/me/branches", owner,
            {"company_id": branch["company_id"], "banner_id": branch["banner_id"],
             "branch_id": branch["branch_id"]})

    email = invited_email()
    invited = api(stack, "POST", "/org/members", owner, {"email": email.upper()})
    stack.users.append(email)
    member = invited.json() or {}
    checks.expect("inviting a member is 201, with the role member and an email sent",
                  invited.status == 201 and member.get("email") == email
                  and member.get("role") == "member" and member.get("invitation_sent") is True,
                  f"{invited.status} {invited.body[:200]}")
    try:
        cognito_user = stack.cognito.admin_get_user(UserPoolId=stack.pool_id, Username=email)
    except stack.cognito.exceptions.UserNotFoundException:
        cognito_user = {}
    attributes = {a["Name"]: a["Value"] for a in cognito_user.get("UserAttributes", [])}
    checks.expect("it creates the user in Cognito, waiting for its temporary password",
                  cognito_user.get("UserStatus") == "FORCE_CHANGE_PASSWORD",
                  str(cognito_user.get("UserStatus")))
    checks.expect("with its email verified, so it can recover its password",
                  attributes.get("email_verified") == "true", str(attributes))

    members = api(stack, "GET", "/org/members", owner)
    rows = members.json() if members.status == 200 else []
    listed = {row.get("email"): row.get("role") for row in rows}
    checks.expect("the org's members list the member next to the owner",
                  members.status == 200 and listed.get(email) == "member"
                  and "owner" in listed.values(), f"{members.status} {listed}")
    again = api(stack, "POST", "/org/members", owner, {"email": email})
    checks.expect("inviting the same email again is 409", again.status == 409, f"{again.status}")
    other = owner_of_new_org(stack, "LGDC verify, roles, otra")
    stolen = api(stack, "POST", "/org/members", other, {"email": email})
    checks.expect("and so is inviting it from another org", stolen.status == 409,
                  f"{stolen.status}")
    bogus = api(stack, "POST", "/org/members", owner, {"email": "no-es-un-email"})
    checks.expect("an invalid email is 422", bogus.status == 422, f"{bogus.status}")

    if not cognito_user:
        return
    token = first_login(checks, stack, email)
    if token is None:
        return
    me = api(stack, "GET", "/auth/me", token).json() or {}
    checks.expect("and then the member is in the owner's org, as member",
                  me.get("org_id") == org_id and me.get("role") == "member", str(me))
    theirs = api(stack, "GET", "/me/branches", token)
    owners = api(stack, "GET", "/me/branches", owner).json()
    # The branch the owner followed, if the stack has any loaded.
    checks.expect("and sees the org's data", theirs.status == 200 and theirs.json() == owners
                  and len(owners or []) == (0 if branch is None else 1),
                  f"{theirs.status} {theirs.body[:200]}")

    refused = {f"{method} {path}": api(stack, method, path, token, body).status
               for method, path, body in OWNER_ACTIONS}
    checks.expect(f"the {len(OWNER_ACTIONS)} owner actions are 403 for the member",
                  all(status == 403 for status in refused.values()), str(refused))
    allowed = {f"{method} {path}": api(stack, method, path, owner).status
               for method, path, _ in OWNER_ACTIONS if method == "GET"}
    checks.expect("and 200 for the owner", all(status == 200 for status in allowed.values()),
                  str(allowed))
    admin_actions = statuses(stack, token, ADMIN_ACTIONS, "POST")
    checks.expect("and so is /admin/*", all(status == 403 for status in admin_actions.values()),
                  str(admin_actions))

    # Someone with no org yet joins by invitation: a Cognito user who never
    # opened the portal (also what a retry finds after a timeout left the
    # Cognito user behind), and an LGDC user who never named an org.
    for who, logs_in_first in (("a Cognito user who never opened the portal", False),
                               ("a user who never named an org", True)):
        email, password = new_user(stack)
        their_token = id_token(stack, email, password)
        if logs_in_first:
            api(stack, "GET", "/auth/me", their_token)
        joined = api(stack, "POST", "/org/members", owner, {"email": email})
        body = joined.json() or {}
        me = api(stack, "GET", "/auth/me", their_token).json() or {}
        checks.expect(f"inviting {who} adds it to the org, with no email sent",
                      joined.status == 201 and body.get("invitation_sent") is False
                      and me.get("org_id") == org_id and me.get("role") == "member",
                      f"{joined.status} {joined.body[:200]} {me}")


def check_platform_admin(checks: Checks, stack: Stack) -> None:
    checks.section("Platform admin · /admin/* answers only to the platform-admin group")
    owner = owner_of_new_org(stack, "LGDC verify, admin")
    refused = statuses(stack, owner, ADMIN_ACTIONS, "POST")
    checks.expect("an org's owner outside the group gets 403 on /admin/*",
                  all(status == 403 for status in refused.values()), str(refused))
    no_org = id_token(stack, *new_user(stack))
    api(stack, "GET", "/auth/me", no_org)
    refused = statuses(stack, no_org, ADMIN_ACTIONS, "POST")
    checks.expect("and so does a user with no org", all(status == 403 for status in refused.values()),
                  str(refused))

    normalized = api(stack, "POST", "/admin/catalog/normalize", platform_admin(stack))
    checks.expect("someone in the group can, even with no org",
                  normalized.status == 200 and "products" in (normalized.json() or {}),
                  f"{normalized.status} {normalized.body[:200]}")


def bootstrap_platform_admin(email: str, password: str) -> subprocess.CompletedProcess[str]:
    """The step Deploy runs after the apply, with the BOOTSTRAP_* secrets."""
    environment = {**os.environ, "BOOTSTRAP_EMAIL": email, "BOOTSTRAP_PASSWORD": password}
    return subprocess.run(["make", "--no-print-directory", "-C", ROOT,
                           "bootstrap-platform-admin"],
                          env=environment, capture_output=True, text=True)


def check_bootstrap(checks: Checks, stack: Stack) -> None:
    checks.section("Deploy · the platform admin from the BOOTSTRAP_* secrets")
    skipped = bootstrap_platform_admin("", "")
    checks.expect("with no BOOTSTRAP_EMAIL the step does nothing and succeeds",
                  skipped.returncode == 0, skipped.stderr[-300:])

    email, password = random_email(), random_password()
    first = bootstrap_platform_admin(email, password)
    stack.users.append(email)
    checks.expect("with it, the step succeeds", first.returncode == 0,
                  (first.stdout + first.stderr)[-300:])
    second = bootstrap_platform_admin(email, password)
    checks.expect("and running it again over the existing platform admin succeeds too",
                  second.returncode == 0, (second.stdout + second.stderr)[-300:])
    try:
        groups = stack.cognito.admin_list_groups_for_user(
            UserPoolId=stack.pool_id, Username=email)["Groups"]
    except stack.cognito.exceptions.UserNotFoundException:
        groups = []
    checks.expect("the platform admin is in platform-admin",
                  [group["GroupName"] for group in groups] == [PLATFORM_ADMIN], str(groups))
    try:
        token = id_token(stack, email, password)
    # KeyError: Cognito answered with a challenge (a new password) instead.
    except (stack.cognito.exceptions.ClientError, KeyError) as error:
        checks.expect("and logs in with BOOTSTRAP_PASSWORD, no new password asked", False,
                      str(error))
        return
    checks.expect("and logs in with BOOTSTRAP_PASSWORD, no new password asked", True)
    normalized = api(stack, "POST", "/admin/catalog/normalize", token)
    checks.expect("and /admin/* takes its token", normalized.status == 200,
                  f"{normalized.status} {normalized.body[:200]}")


def run(checks: Checks, stack: Stack) -> None:
    check_invitation(checks, stack)
    check_platform_admin(checks, stack)
    check_bootstrap(checks, stack)
