"""The invitation's Cognito half: creates the invited member's user.

The API, inside the VPC, cannot reach Cognito: a user pool with a domain
(the Hosted UI's) takes no request through a VPC endpoint, and the VPC has
no way out to the internet. So the API invokes this Lambda, outside the VPC,
through the Lambda endpoint, and waits for its answer (docs/adr/0003).

Cognito emails the member a temporary password, with the message the user
pool sets (infra/modules/auth), and asks for a new one on the first login.
The email is marked verified: the owner typed it, and the member proves it
by logging in with what was sent there.

    handler({"email": "ana@example.com"}, context)
      -> {"status": "created", "sub": "..."}   emailed a temporary password
       | {"status": "exists", "sub": "..."}    Cognito already had that user:
                                               no email, it keeps its password
       | {"status": "invalid"}                 Cognito refused the email

Asking twice for the same email answers `exists` the second time, so the
API can retry an invocation whose answer got lost.

Anything else raises, and the API sees a FunctionError.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import boto3

log = logging.getLogger("invite")
log.setLevel(logging.INFO)

USER_POOL_ID = os.environ["USER_POOL_ID"]

cognito = boto3.client("cognito-idp")


def handler(event: dict[str, Any], context: Any) -> dict[str, str]:
    email = event["email"]
    try:
        user = cognito.admin_create_user(
            UserPoolId=USER_POOL_ID, Username=email, DesiredDeliveryMediums=["EMAIL"],
            UserAttributes=[{"Name": "email", "Value": email},
                            {"Name": "email_verified", "Value": "true"}])["User"]
    except cognito.exceptions.UsernameExistsException:
        existing = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=email)
        return {"status": "exists", "sub": attribute(existing["UserAttributes"], "sub")}
    except cognito.exceptions.InvalidParameterException as error:
        log.info("Cognito refused the email: %s", error)
        return {"status": "invalid"}
    sub = attribute(user["Attributes"], "sub")
    log.info("invited %s", sub)
    return {"status": "created", "sub": sub}


def attribute(attributes: list[dict[str, str]], name: str) -> str:
    return next(a["Value"] for a in attributes if a["Name"] == name)
