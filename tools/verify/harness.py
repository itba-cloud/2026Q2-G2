"""What every suite shares: the report, the deployed stack and its doors.

The stack comes from `terraform output`, the same values Deploy writes to the
job summary. Whatever a check creates outside Postgres registers how to undo
it on the Stack, and main.py undoes it all at the end, even after a crash.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import boto3

GREEN, RED, GREY, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGION = os.getenv("AWS_REGION", "us-east-1")

# Every screen's first read, and the auth's own: none may answer anonymously,
# and all answer 200 to an owner.
SCREEN_READS = (
    "/auth/me",
    "/feed-dates",
    "/branches?limit=5",
    "/me/branches",
    "/me/merchant",
    "/products?q=leche",
    "/alerts",
    "/alerts/unread-count",
    "/rules",
    "/uploads",
    "/ingest/jobs",
)


@dataclass
class Checks:
    passed: int = 0
    failed: list[str] = field(default_factory=list)

    def section(self, title: str) -> None:
        print(f"\n{BOLD}{title}{RESET}", flush=True)

    def expect(self, description: str, holds: bool, detail: str = "") -> None:
        if holds:
            self.passed += 1
            print(f"  {GREEN}✓{RESET} {description}", flush=True)
            return
        self.failed.append(description)
        print(f"  {RED}✗{RESET} {description}", flush=True)
        if detail:
            print(f"{GREY}      {detail}{RESET}", flush=True)

    def note(self, text: str) -> None:
        print(f"  {GREY}· {text}{RESET}", flush=True)

    def report(self) -> int:
        total = self.passed + len(self.failed)
        print()
        if not self.failed:
            print(f"{GREEN}{BOLD}{total} checks, all good.{RESET}")
            return 0
        print(f"{RED}{BOLD}{len(self.failed)} of {total} checks failed:{RESET}")
        for description in self.failed:
            print(f"  {RED}·{RESET} {description}")
        return 1


@dataclass
class Stack:
    """The deployed stack: Terraform's outputs, the AWS clients, and what the
    run created, to undo at the end."""
    outputs: dict[str, Any]
    cognito: Any
    s3: Any
    sqs: Any
    ecs: Any
    lambda_: Any
    events: Any
    sns: Any
    logs: Any
    dynamodb: Any
    # Every Cognito user the checks created, deleted at the end.
    users: list[str] = field(default_factory=list)
    # Every S3 prefix the checks wrote under, emptied (all versions) at the end.
    prefixes: list[str] = field(default_factory=list)
    # Every SNS subscription and SQS queue the checks created (the probes of
    # the events suite), unsubscribed and deleted at the end.
    subscriptions: list[str] = field(default_factory=list)
    queues: list[str] = field(default_factory=list)
    # The most worker tasks seen running at once while the checks waited.
    most_running: int = 0

    @property
    def api(self) -> str:
        return self.outputs["api_endpoint"]

    @property
    def bucket(self) -> str:
        return self.outputs["data_bucket_name"]

    @property
    def pool_id(self) -> str:
        return self.outputs["cognito_user_pool_id"]

    @property
    def client_id(self) -> str:
        return self.outputs["cognito_client_id"]

    @property
    def client_secret(self) -> str:
        return self.outputs["cognito_client_secret"]


def terraform_outputs() -> dict[str, Any]:
    raw = subprocess.run(
        ["terraform", f"-chdir={ROOT}/infra", "output", "-json"],
        check=True, capture_output=True, text=True).stdout
    return {name: output["value"] for name, output in json.loads(raw).items()}


def connect() -> Stack:
    return Stack(
        outputs=terraform_outputs(),
        cognito=boto3.client("cognito-idp", region_name=REGION),
        s3=boto3.client("s3", region_name=REGION),
        sqs=boto3.client("sqs", region_name=REGION),
        ecs=boto3.client("ecs", region_name=REGION),
        lambda_=boto3.client("lambda", region_name=REGION),
        events=boto3.client("events", region_name=REGION),
        sns=boto3.client("sns", region_name=REGION),
        logs=boto3.client("logs", region_name=REGION),
        dynamodb=boto3.client("dynamodb", region_name=REGION),
    )


@dataclass
class Response:
    status: int
    body: str
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands a redirect back as the response, so its Location can be read."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def fetch(url: str, method: str = "GET", body: bytes | None = None,
          headers: dict[str, str] | None = None,
          opener: urllib.request.OpenerDirector | None = None) -> Response:
    request = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with (opener or urllib.request.build_opener()).open(request, timeout=35) as response:
            return Response(response.status, response.read().decode(),
                            {k.lower(): v for k, v in response.headers.items()})
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read().decode(errors="replace"),
                        {k.lower(): v for k, v in error.headers.items()})


def api(stack: Stack, method: str, path: str, token: str | None = None,
        body: dict[str, Any] | None = None) -> Response:
    """The public API, the way the portal calls it."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return fetch(f"{stack.api}/api{path}", method,
                 json.dumps(body).encode() if body is not None else None, headers)


def statuses(stack: Stack, token: str | None, paths: Iterable[str],
             method: str = "GET") -> dict[str, int]:
    """Each path's status, for the checks that ask many doors the same thing."""
    return {path: api(stack, method, path, token).status for path in paths}


def running_tasks(stack: Stack) -> tuple[int, int]:
    """The workers' service: its desired and running counts."""
    service = stack.ecs.describe_services(
        cluster=stack.outputs["worker_cluster_name"],
        services=[stack.outputs["worker_service_name"]])["services"][0]
    return service["desiredCount"], service["runningCount"]


def wait_for(stack: Stack, condition: Callable[[], Any], timeout: float,
             interval: float = 10) -> Any:
    """Poll until condition() returns something truthy, or None at the timeout.
    Every wait in the checks is a wait on the workers, so it also records the
    most of them seen running, which the scale-out check reads."""
    deadline = time.monotonic() + timeout
    while True:
        stack.most_running = max(stack.most_running, running_tasks(stack)[1])
        if result := condition():
            return result
        if time.monotonic() > deadline:
            return None
        time.sleep(interval)


def object_versions(stack: Stack, prefix: str) -> list[dict[str, str]]:
    """Every version and delete marker under `prefix`, as delete_objects takes them."""
    found = []
    for page in stack.s3.get_paginator("list_object_versions").paginate(
            Bucket=stack.bucket, Prefix=prefix):
        for entry in page.get("Versions", []) + page.get("DeleteMarkers", []):
            found.append({"Key": entry["Key"], "VersionId": entry["VersionId"]})
    return found


def clean_up(stack: Stack) -> None:
    """Undo what the run created outside Postgres. Never raises: a failed
    undo is reported by the clean-up checks in main.py."""
    for email in stack.users:
        try:
            stack.cognito.admin_delete_user(UserPoolId=stack.pool_id, Username=email)
        except stack.cognito.exceptions.UserNotFoundException:
            pass
        except Exception as error:  # keep undoing the rest
            print(f"{GREY}      could not delete {email}: {error}{RESET}")
    for prefix in stack.prefixes:
        try:
            versions = object_versions(stack, prefix)
            for start in range(0, len(versions), 1000):
                stack.s3.delete_objects(Bucket=stack.bucket,
                                        Delete={"Objects": versions[start:start + 1000]})
        except Exception as error:  # keep undoing the rest
            print(f"{GREY}      could not empty {prefix}: {error}{RESET}")
    for subscription in stack.subscriptions:
        try:
            stack.sns.unsubscribe(SubscriptionArn=subscription)
        except Exception as error:  # keep undoing the rest
            print(f"{GREY}      could not unsubscribe {subscription}: {error}{RESET}")
    for queue_url in stack.queues:
        try:
            stack.sqs.delete_queue(QueueUrl=queue_url)
        except Exception as error:  # keep undoing the rest
            print(f"{GREY}      could not delete {queue_url}: {error}{RESET}")
