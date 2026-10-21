"""The jobs pipeline (ticket 04).

A SEPA archive dropped in `raw/` ends up as prices the API serves and as
Parquet in `curated/`; the same message delivered twice loads nothing twice;
a broken file goes to `rejected/` without a retry; a transient error is
retried and lands in the DLQ on the third failure; and the workers scale out
with messages and back to zero without them (`check_scale_in`, which main.py
runs last, so the other suites that load chains find the workers up).

Its chains (899000001 to 899000003) have no coordinates, so they never enter
a comparison.
"""
from __future__ import annotations

import io
import json
import uuid
from typing import Any

import pyarrow.parquet

from archives import (FEED_DATE, JOB_TIMEOUT, chain_key, delete_chain_files_at_end, job,
                      processed_after, put_raw, raw_key, sepa_archive)
from cognito import owner_of_new_org
from harness import Checks, Stack, api, running_tasks, wait_for

GOOD_CHAIN = 899000001
BROKEN_CHAIN = 899000002
TRANSIENT_CHAIN = 899000003

# One unusable row out of fifty, under the 5% that fails the whole file.
PRICES = {f"77999990{index:05d}": 100 + index + 0.5 for index in range(49)}
REJECTED = 1

SCALE_IN_TIMEOUT = 20 * 60


def queue_is_empty(stack: Stack, queue_url: str) -> bool:
    attributes = stack.sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
        ])["Attributes"]
    return all(int(count) == 0 for count in attributes.values())


def jobs_queue_is_empty(stack: Stack) -> bool:
    return queue_is_empty(stack, stack.outputs["jobs_queue_url"])


def s3_event(bucket: str, key: str, version_id: str) -> str:
    """The body S3 Event Notifications sends to the queue for a new object."""
    return json.dumps({"Records": [{
        "eventSource": "aws:s3",
        "eventName": "ObjectCreated:Put",
        "s3": {"bucket": {"name": bucket},
               "object": {"key": key, "versionId": version_id}},
    }]})


def object_exists(stack: Stack, key: str) -> bool:
    return "Contents" in stack.s3.list_objects_v2(Bucket=stack.bucket, Prefix=key)


def branch_prices(stack: Stack, token: str) -> list[dict[str, Any]]:
    return api(stack, "GET", f"/branches/{GOOD_CHAIN}/1/1/prices"
                             f"?feed_date={FEED_DATE}&limit=500", token).json() or []


def check_idle(checks: Checks, stack: Stack) -> None:
    checks.section("Scaling · no messages, no workers")
    if not jobs_queue_is_empty(stack):
        checks.note("the jobs queue has messages: skipped")
        return
    # A load just before the run (a CSV from the portal, say) leaves workers
    # up until the idle alarm takes them back to 0, minutes after its last
    # message: wait for that, as check_scale_in does.
    idle = wait_for(stack, lambda: running_tasks(stack) == (0, 0), SCALE_IN_TIMEOUT,
                    interval=30)
    # Those workers were not this run's: the scale-out check counts from here.
    stack.most_running = 0
    checks.expect("with the queue empty the service goes to 0 tasks", bool(idle),
                  f"desired, running: {running_tasks(stack)}")


def check_load(checks: Checks, stack: Stack, token: str) -> dict[str, Any] | None:
    checks.section("Load · a SEPA archive in raw/ becomes prices")
    previous = job(stack, token, GOOD_CHAIN)
    put_raw(stack, GOOD_CHAIN, "verify.zip", sepa_archive(GOOD_CHAIN, PRICES, REJECTED))

    done = wait_for(stack, processed_after(stack, token, GOOD_CHAIN, previous, "done"),
                    JOB_TIMEOUT)
    checks.expect("the job ends in done", done is not None,
                  f"last seen: {job(stack, token, GOOD_CHAIN)}")
    if done is None:
        return None
    checks.expect(f"it counted {len(PRICES)} prices and {REJECTED} rejected row",
                  (done["rows_ok"], done["rows_rejected"]) == (len(PRICES), REJECTED),
                  f"rows_ok {done['rows_ok']}, rows_rejected {done['rows_rejected']}")

    api(stack, "POST", "/me/branches", token,
        {"company_id": GOOD_CHAIN, "banner_id": 1, "branch_id": "1"})
    prices = branch_prices(stack, token)
    by_ean = {row["ean"]: float(row["price"]) for row in prices}
    checks.expect(f"the API serves the {len(PRICES)} prices", len(prices) == len(PRICES),
                  f"got {len(prices)}")
    checks.expect("with the price the file said",
                  by_ean.get("7799999000007") == 107.5, f"got {by_ean.get('7799999000007')}")

    curated = stack.s3.get_object(
        Bucket=stack.bucket,
        Key=chain_key("curated", GOOD_CHAIN, "prices.parquet"))
    table = pyarrow.parquet.read_table(io.BytesIO(curated["Body"].read()))
    checks.expect(f"curated/ holds the {len(PRICES)} prices as Parquet",
                  table.num_rows == len(PRICES), f"got {table.num_rows}")

    rejected = stack.s3.get_object(
        Bucket=stack.bucket,
        Key=chain_key("rejected", GOOD_CHAIN, "rejected.jsonl"))
    lines = [json.loads(line) for line in rejected["Body"].read().splitlines()]
    checks.expect("rejected/ says which row was dropped and why",
                  len(lines) == REJECTED and all(line.get("reason") for line in lines),
                  f"got {lines}")
    return done


def check_redelivery(checks: Checks, stack: Stack, token: str,
                     done: dict[str, Any] | None) -> None:
    checks.section("Redelivery · the same message twice loads nothing twice")
    if done is None:
        checks.expect("the load finished, to deliver its message again", False)
        return
    key = raw_key(GOOD_CHAIN, "verify.zip")
    version = stack.s3.head_object(Bucket=stack.bucket, Key=key)["VersionId"]
    stack.sqs.send_message(QueueUrl=stack.outputs["jobs_queue_url"],
                           MessageBody=s3_event(stack.bucket, key, version))

    drained = wait_for(stack, lambda: jobs_queue_is_empty(stack), JOB_TIMEOUT)
    checks.expect("a worker consumed the copy", bool(drained))
    after = job(stack, token, GOOD_CHAIN)
    checks.expect("the job was not run again",
                  after is not None and after["updated_at"] == done["updated_at"]
                  and after["attempts"] == done["attempts"],
                  f"before {done}, after {after}")
    prices = branch_prices(stack, token)
    checks.expect(f"the API still serves {len(PRICES)} prices", len(prices) == len(PRICES),
                  f"got {len(prices)}")


def check_broken_file(checks: Checks, stack: Stack, token: str) -> None:
    checks.section("Broken file · to rejected/, no retry")
    previous = job(stack, token, BROKEN_CHAIN)
    put_raw(stack, BROKEN_CHAIN, "broken.zip", b"this is not a zip")

    failed = wait_for(stack, processed_after(stack, token, BROKEN_CHAIN, previous, "failed"),
                      JOB_TIMEOUT)
    checks.expect("the job ends in failed", failed is not None,
                  f"last seen: {job(stack, token, BROKEN_CHAIN)}")
    if failed is None:
        return
    checks.expect("with the reason", bool(failed["last_error"]), str(failed))
    checks.expect("the file is in rejected/",
                  object_exists(stack, chain_key("rejected", BROKEN_CHAIN, "broken.zip")))
    wait_for(stack, lambda: jobs_queue_is_empty(stack), JOB_TIMEOUT)
    after = job(stack, token, BROKEN_CHAIN)
    checks.expect("it was tried once", after is not None and after["attempts"] == 1,
                  str(after))


def take_from_dlq(stack: Stack, key: str) -> bool:
    """Find the message for `key` in the DLQ and delete it, so the DLQ stays
    clean for the next run."""
    dlq_url = stack.outputs["jobs_dlq_url"]
    received = stack.sqs.receive_message(
        QueueUrl=dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=5,
        VisibilityTimeout=30).get("Messages", [])
    for message in received:
        if key in message["Body"]:
            stack.sqs.delete_message(QueueUrl=dlq_url, ReceiptHandle=message["ReceiptHandle"])
            return True
    return False


def check_transient_error(checks: Checks, stack: Stack, token: str) -> None:
    checks.section("Transient error · retried, to the DLQ on the third failure")
    previous = job(stack, token, TRANSIENT_CHAIN)
    # An object that is not there: the download fails, and nothing about the
    # failure says the file is broken.
    key = raw_key(TRANSIENT_CHAIN, "missing.zip")
    delete_chain_files_at_end(stack, TRANSIENT_CHAIN)
    stack.sqs.send_message(QueueUrl=stack.outputs["jobs_queue_url"],
                           MessageBody=s3_event(stack.bucket, key, uuid.uuid4().hex))

    failed = wait_for(stack, processed_after(stack, token, TRANSIENT_CHAIN, previous, "failed"),
                      JOB_TIMEOUT)
    checks.expect("the job ends in failed", failed is not None,
                  f"last seen: {job(stack, token, TRANSIENT_CHAIN)}")
    if failed is None:
        return
    checks.expect("after three attempts", failed["attempts"] == 3, str(failed))
    in_dlq = wait_for(stack, lambda: take_from_dlq(stack, key), JOB_TIMEOUT, interval=1)
    checks.expect("the message is in the DLQ", bool(in_dlq))


def check_scale_in(checks: Checks, stack: Stack) -> None:
    checks.section("Scaling · out with messages, back to zero without them")
    checks.expect("workers ran while there were messages", stack.most_running > 0,
                  f"most running at once: {stack.most_running}")
    idle = wait_for(stack, lambda: running_tasks(stack) == (0, 0), SCALE_IN_TIMEOUT,
                    interval=30)
    checks.expect("the service is back to 0 tasks", bool(idle),
                  f"desired, running: {running_tasks(stack)}")


def run(checks: Checks, stack: Stack) -> None:
    # The job list and the prices need a session.
    token = owner_of_new_org(stack, "LGDC verify, pipeline")
    check_idle(checks, stack)
    done = check_load(checks, stack, token)
    check_redelivery(checks, stack, token, done)
    check_broken_file(checks, stack, token)
    check_transient_error(checks, stack, token)
