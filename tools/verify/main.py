"""`make verify`: the TP3's checks, against the deployed stack.

It goes through the stack's own doors, the ones a user or the course staff
use: the portal's URL, the Hosted UI, the public API with a Cognito token,
the data bucket's raw/ (where the ingestion drops a chain's file),
`make ingest`, what run-ingest runs, and `make bootstrap-platform-admin`, the
step Deploy runs. It reads the stack
from `terraform output`, so it needs `make init` done and the same AWS
credentials as `make plan`.

    python tools/verify/main.py                  every suite, in this order
    python tools/verify/main.py portal roles     only those

It creates its own users and chains, and undoes at the end what it created
outside Postgres: the Cognito users, the chains' files in the data bucket,
and the probe queues the events suite subscribes to the topic.
Exits non-zero if any check failed. It does not run in CI.
"""
from __future__ import annotations

import sys
import traceback
from typing import Callable

import close
import competitors
import events
import ingest
import pipeline
import portal
import precompute
import roles
import schema
import uploads
from harness import Checks, Stack, clean_up, connect, object_versions

# In the order they run. The pipeline goes before the ingestion, the uploads,
# the competitive set and the notices, which load their files through the
# same workers, and its scale-in check last of all, once every suite is done
# with them.
SUITES: dict[str, Callable[[Checks, Stack], None]] = {
    "schema": schema.run,
    "portal": portal.run,
    "pipeline": pipeline.run,
    "ingest": ingest.run,
    "uploads": uploads.run,
    "competitors": competitors.run,
    "events": events.run,
    "close": close.run,
    "precompute": precompute.run,
    "roles": roles.run,
}


def run_suite(checks: Checks, name: str, suite: Callable[[], None]) -> None:
    """A suite that crashes is one failed check, and the others still run."""
    try:
        suite()
    except Exception:  # the report says which suite, and why
        checks.expect(f"the {name} suite ran to the end", False,
                      traceback.format_exc(limit=-3).strip())


def user_exists(stack: Stack, email: str) -> bool:
    try:
        stack.cognito.admin_get_user(UserPoolId=stack.pool_id, Username=email)
        return True
    except stack.cognito.exceptions.UserNotFoundException:
        return False


def queue_exists(stack: Stack, queue_url: str) -> bool:
    try:
        stack.sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
        return True
    except stack.sqs.exceptions.QueueDoesNotExist:
        return False


def subscription_exists(stack: Stack, arn: str) -> bool:
    try:
        stack.sns.get_subscription_attributes(SubscriptionArn=arn)
        return True
    except stack.sns.exceptions.NotFoundException:
        return False


def check_clean_up(checks: Checks, stack: Stack) -> None:
    checks.section("Clean-up · nothing the run created is left outside Postgres")
    left = [email for email in stack.users if user_exists(stack, email)]
    checks.expect(f"the {len(stack.users)} test users are gone from Cognito", not left,
                  ", ".join(left))
    files = [prefix for prefix in stack.prefixes if object_versions(stack, prefix)]
    checks.expect(f"the test chains' files are gone from the data bucket "
                  f"({len(stack.prefixes)} prefixes, every version)", not files,
                  ", ".join(files))
    if stack.queues:
        queues = [url for url in stack.queues if queue_exists(stack, url)]
        checks.expect(f"the {len(stack.queues)} probe queues are gone", not queues,
                      ", ".join(queues))
    if stack.subscriptions:
        left = [arn for arn in stack.subscriptions if subscription_exists(stack, arn)]
        checks.expect(f"and their {len(stack.subscriptions)} subscriptions to the topic",
                      not left, ", ".join(left))


def main(names: list[str]) -> int:
    unknown = [name for name in names if name not in SUITES]
    if unknown:
        print(f"unknown suite(s): {', '.join(unknown)}; there are: {', '.join(SUITES)}",
              file=sys.stderr)
        return 2
    chosen = [name for name in SUITES if name in names] if names else list(SUITES)

    stack = connect()
    checks = Checks()
    try:
        for name in chosen:
            run_suite(checks, name, lambda: SUITES[name](checks, stack))
        if "pipeline" in chosen:
            run_suite(checks, "pipeline", lambda: pipeline.check_scale_in(checks, stack))
    finally:
        clean_up(stack)
    check_clean_up(checks, stack)
    return checks.report()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
