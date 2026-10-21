"""The day close (ticket 11).

The day-close Lambda consumes the day-close queue, where the cadena-procesada
topic leaves every notice. A chain's notice marks the chain in its day; once
every chain the ingestion stored in raw/ for that day has settled (done or
failed), one invocation wins the close, runs the catalogue maintenance (the
neighbors and the catalogue) and queues one precompute entry per followed
branch and radius. A merchant's upload queues only that merchant's branch,
without closing anything. A cron invokes it at 10:00 with the day's date, and
it closes with what there is.

What it establishes, through the stack's doors: the function consumes the
day-close queue and has its cron; three chains of a day settle and the day
closes once, at the last one, rebuilding the neighbors the portal shows; the
close queues each followed branch and radius; an upload queues its
merchant's branch alone; and the cron's invocation closes a day whose chains
are still loading, and closes it again when they come in.

What the function decided is read from its log, one line per notice and per
cron invocation: the precompute queue is the precompute's, and its entries
cannot be read without taking them.

Every run uses four days of its own, between 100 and 160 days back (within
the months `price` keeps), with no job yet: a day closes once, so a day of an
earlier run, or the real ingestion's, would already be closed. Its chains are
899000041 (home), 899000042 (500 m north) and 899000043 (a broken file), in
the South Atlantic (-55, -45), more than 500 km from every other suite's.
"""
from __future__ import annotations

import datetime
import json
import random
import time
from typing import Any

from archives import JOB_TIMEOUT, job, processed_after, put_raw, sepa_archive
from cognito import owner_of_new_org
from harness import Checks, Stack, api, wait_for
from uploads import portal_origin, post_file, price_list, settled

HOME, RIVAL, BROKEN = 899000041, 899000042, 899000043
LATITUDE, LONGITUDE = -55.0, -45.0
# On earthdistance's sphere 0.0045° of latitude is ~500 m.
RIVAL_NORTH = 0.0045
# The merchant of the upload, 300 km east of the chains: not their neighbor.
MERCHANT_LATITUDE, MERCHANT_LONGITUDE = -55.0, -40.0
PRICES = {f"77999994{index:05d}": 100.0 for index in range(3)}

# The cron's schedule (infra/variables.tf): 10:00 in Buenos Aires.
BACKUP_SCHEDULE = "cron(0 13 * * ? *)"
# The function's result lines start with this.
MARKER = "day-close "
# From the last job's end to its notice's line in the log.
CLOSE_TIMEOUT = 4 * 60
# How long to keep watching for a line that must not come.
GRACE_SECONDS = 30


def free_days(stack: Stack, token: str) -> list[str] | None:
    """Four consecutive days with no job, between 100 and 160 days back."""
    today = datetime.date.today()
    for _ in range(20):
        start = today - datetime.timedelta(days=random.randint(100, 160))
        days = [(start + datetime.timedelta(days=offset)).isoformat() for offset in range(4)]
        if not any(api(stack, "GET", f"/ingest/jobs?feed_date={day}&limit=1", token).json()
                   for day in days):
            return days
    return None


def decisions(stack: Stack, feed_date: str, since_ms: int) -> list[dict[str, Any]]:
    """What the function decided about a day, oldest first: one line per
    notice and per cron invocation."""
    found = []
    paginator = stack.logs.get_paginator("filter_log_events")
    for page in paginator.paginate(
            logGroupName=f"/aws/lambda/{stack.outputs['day_close_function_name']}",
            startTime=since_ms, filterPattern=f'"{MARKER.strip()}" "{feed_date}"'):
        for event in page.get("events", []):
            _, _, text = event["message"].partition(MARKER)
            try:
                line = json.loads(text)
            except json.JSONDecodeError:
                continue
            if line.get("feed_date") == feed_date:
                found.append((event["timestamp"], line))
    return [line for _, line in sorted(found, key=lambda pair: pair[0])]


def notices(stack: Stack, feed_date: str, since_ms: int) -> list[dict[str, Any]]:
    return [line for line in decisions(stack, feed_date, since_ms) if line["trigger"] == "notice"]


def precompute_entry(company_id: int, radius_km: float) -> dict[str, Any]:
    """A precompute entry, as the function logs it: a branch and a radius."""
    return {"company_id": company_id, "banner_id": 1, "branch_id": "1",
            "radius_km": radius_km}


def invoke_as_cron(stack: Stack, feed_date: str) -> dict[str, Any]:
    """Invoke the function the way its cron does, at 10:00 in Buenos Aires of
    that day. Its answer."""
    event = {"version": "0", "id": "verify", "detail-type": "Scheduled Event",
             "source": "aws.events", "time": f"{feed_date}T13:00:00Z",
             "region": "us-east-1", "resources": [], "detail": {}}
    answer = stack.lambda_.invoke(FunctionName=stack.outputs["day_close_function_name"],
                                  Payload=json.dumps(event).encode())
    return json.loads(answer["Payload"].read() or b"{}")


def store_raw(stack: Stack, chains: dict[int, bytes], feed_date: str) -> None:
    for chain, body in chains.items():
        put_raw(stack, chain, "verify.zip", body, feed_date)


def archives() -> dict[int, bytes]:
    return {HOME: sepa_archive(HOME, PRICES, latitude=LATITUDE, longitude=LONGITUDE),
            RIVAL: sepa_archive(RIVAL, PRICES, latitude=LATITUDE + RIVAL_NORTH,
                                longitude=LONGITUDE)}


def wait_until_settled(checks: Checks, stack: Stack, token: str, feed_date: str,
           statuses: dict[int, str]) -> bool:
    ended = {chain: wait_for(stack, processed_after(stack, token, chain, None, status,
                                                     feed_date), JOB_TIMEOUT)
             for chain, status in statuses.items()}
    checks.expect(f"the {len(statuses)} chains' jobs settle: "
                  + ", ".join(f"{chain} {status}" for chain, status in statuses.items()),
                  all(ended.values()),
                  str({chain: job(stack, token, chain, feed_date) for chain in statuses}))
    return all(ended.values())


def wait_for_notices(stack: Stack, feed_date: str, since_ms: int,
                     count: int) -> list[dict[str, Any]]:
    """The day's notice lines, once there are `count`, and a grace period
    later, so that one that must not come has had its chance."""
    wait_for(stack, lambda: len(notices(stack, feed_date, since_ms)) >= count,
             CLOSE_TIMEOUT, interval=5)
    time.sleep(GRACE_SECONDS)
    return notices(stack, feed_date, since_ms)


def check_wiring(checks: Checks, stack: Stack) -> bool:
    checks.section("Day close · the function, its queue and its cron")
    function = stack.outputs.get("day_close_function_name", "")
    checks.expect("the stack has a day-close function", bool(function))
    if not function:
        return False
    mappings = stack.lambda_.list_event_source_mappings(FunctionName=function)
    consumes = [mapping for mapping in mappings["EventSourceMappings"]
                if mapping["EventSourceArn"] == stack.outputs["day_close_queue_arn"]]
    checks.expect("it consumes the day-close queue, at most 2 at a time, reporting "
                  "which notices of a batch failed",
                  len(consumes) == 1 and consumes[0]["State"] == "Enabled"
                  and consumes[0].get("ScalingConfig", {}).get("MaximumConcurrency") == 2
                  and "ReportBatchItemFailures" in consumes[0].get("FunctionResponseTypes", []),
                  str(mappings["EventSourceMappings"]))

    rule = stack.events.describe_rule(Name=function)
    targets = stack.events.list_targets_by_rule(Rule=function)["Targets"]
    arn = stack.lambda_.get_function(FunctionName=function)["Configuration"]["FunctionArn"]
    checks.expect(f"a cron invokes it every day at 10:00 in Buenos Aires ({BACKUP_SCHEDULE})",
                  rule["State"] == "ENABLED" and rule["ScheduleExpression"] == BACKUP_SCHEDULE
                  and [target["Arn"] for target in targets] == [arn],
                  f"{rule.get('State')} {rule.get('ScheduleExpression')} {targets}")

    queue = stack.sqs.get_queue_attributes(QueueUrl=stack.outputs["precompute_queue_url"],
                                           AttributeNames=["RedrivePolicy"])["Attributes"]
    redrive = json.loads(queue.get("RedrivePolicy", "{}"))
    checks.expect("the precompute queue exists, with its DLQ after 3 receives",
                  redrive.get("maxReceiveCount") == 3, str(queue))
    return True


def check_last_chain(checks: Checks, stack: Stack, token: str, day: str,
                     since_ms: int) -> bool:
    checks.section(f"Day close · three chains of {day}, the day closes once, at the last")
    store_raw(stack, {**archives(), BROKEN: b"this is not a zip"}, day)
    if not wait_until_settled(checks, stack, token, day,
                              {HOME: "done", RIVAL: "done", BROKEN: "failed"}):
        return False
    seen = wait_for_notices(stack, day, since_ms, 3)
    checks.expect("the function gets the three chains' notices",
                  sorted(line.get("company_id") for line in seen) == [HOME, RIVAL, BROKEN],
                  str(seen))
    closes = [line for line in seen if line["closed"]]
    checks.expect("exactly one of them closes the day", len(closes) == 1, str(seen))
    checks.expect("the one that closes it sees the three chains stored, all settled",
                  len(closes) == 1 and closes[0]["stored"] == closes[0]["settled"] == 3,
                  str(closes))

    followed = api(stack, "POST", "/me/branches", token,
                   {"company_id": HOME, "banner_id": 1, "branch_id": "1", "radius_km": 1})
    checks.expect("the org follows the home branch, at 1 km", followed.status == 201,
                  f"{followed.status} {followed.body[:200]}")
    path = f"/branches/{HOME}/1/1/competitors?radius_km=1&limit=10"
    found = wait_for(stack, lambda: (api(stack, "GET", path, token).json() or {})
                     .get("total") == 1, 60, interval=5)
    competitors = api(stack, "GET", path, token).json() or {}
    near = [(item["company_id"], item["distance_m"]) for item in competitors.get("items", [])]
    checks.expect("the close rebuilt the neighbors: the rival is 500 m away",
                  bool(found) and len(near) == 1 and near[0][0] == RIVAL
                  and abs(near[0][1] - 500) < 25, str(competitors))
    return True


def check_entries(checks: Checks, stack: Stack, token: str, other: str, day: str,
                  since_ms: int) -> None:
    checks.section(f"Day close · {day} queues one precompute per followed branch and radius")
    for body in ({"company_id": HOME, "banner_id": 1, "branch_id": "1", "radius_km": 5},
                 {"company_id": RIVAL, "banner_id": 1, "branch_id": "1", "radius_km": 5}):
        followed = api(stack, "POST", "/me/branches", other, body)
        checks.expect(f"another org follows {body['company_id']} at 5 km",
                      followed.status == 201, f"{followed.status} {followed.body[:200]}")
    store_raw(stack, archives(), day)
    if not wait_until_settled(checks, stack, token, day, {HOME: "done", RIVAL: "done"}):
        return
    seen = wait_for_notices(stack, day, since_ms, 2)
    closes = [line for line in seen if line["closed"]]
    checks.expect("the day closes once", len(closes) == 1, str(seen))
    if len(closes) != 1:
        return
    queued = closes[0]["precompute"]
    expected = [precompute_entry(HOME, 1.0), precompute_entry(HOME, 5.0),
                precompute_entry(RIVAL, 5.0)]
    checks.expect("it queues the home branch at 1 and at 5 km, and the rival at 5 km",
                  all(item in queued for item in expected),
                  str([item for item in queued if item["company_id"] in (HOME, RIVAL)]))
    checks.expect(f"each followed branch and radius once ({len(queued)} entries)",
                  len(queued) == len({tuple(item.values()) for item in queued}))


def check_upload(checks: Checks, stack: Stack, token: str, day: str, since_ms: int) -> None:
    checks.section(f"Day close · an upload for {day} queues only its merchant's branch")
    merchant = api(stack, "POST", "/me/merchant", token, {
        "name": "LGDC verify, cierre", "province": "AR-C", "locality": "CABA",
        "latitude": MERCHANT_LATITUDE, "longitude": MERCHANT_LONGITUDE}).json() or {}
    company = merchant.get("company_id")
    started = api(stack, "POST", "/uploads", token,
                  {"feed_date": day, "filename": "verify.csv"}).json() or {}
    upload_id = started.get("upload_id")
    checks.expect("the org sets up its own branch and starts an upload",
                  bool(company) and bool(upload_id), f"{merchant} {started}")
    if not company or not upload_id:
        return
    stack.prefixes.extend([f"raw/uploads/{upload_id}/", f"rejected/uploads/{upload_id}/",
                           f"curated/feed_date={day}/chain_id={company}/"])
    post_file(started, "verify.csv", price_list(), portal_origin(stack))
    done = wait_for(stack, settled(stack, token, upload_id, "done"), JOB_TIMEOUT)
    checks.expect("the upload ends in done", done is not None)
    if done is None:
        return
    seen = wait_for_notices(stack, day, since_ms, 1)
    checks.expect("the function gets its notice, as an upload",
                  [line.get("kind") for line in seen] == ["upload"], str(seen))
    if not seen:
        return
    checks.expect("it queues the merchant's branch at its radius, and nothing else",
                  seen[0]["precompute"] == [precompute_entry(company, 5.0)], str(seen[0]))
    checks.expect("it does not close the day", not seen[0]["closed"], str(seen[0]))
    answer = invoke_as_cron(stack, day)
    checks.expect("the day has no chain to close: the cron finds nothing stored",
                  answer.get("stored") == 0 and answer.get("closed") is False, str(answer))


def check_backup(checks: Checks, stack: Stack, token: str, day: str, since_ms: int) -> None:
    checks.section(f"Day close · the cron closes {day} with what there is")
    store_raw(stack, archives(), day)
    # Right after the files land: a worker takes minutes to load a chain,
    # the invocation a second or two to decide.
    answer = invoke_as_cron(stack, day)
    checks.expect("with the two chains still loading, the cron closes the day",
                  answer.get("trigger") == "backup" and answer.get("closed") is True
                  and answer.get("stored") == 2 and answer.get("settled", 2) < 2, str(answer))
    checks.expect("and queues the followed branches",
                  precompute_entry(HOME, 1.0) in answer.get("precompute", []), str(answer))
    again = invoke_as_cron(stack, day)
    checks.expect("the cron again finds the day closed, and does nothing",
                  again.get("closed") is False and not again.get("precompute"), str(again))
    if not wait_until_settled(checks, stack, token, day, {HOME: "done", RIVAL: "done"}):
        return
    seen = wait_for_notices(stack, day, since_ms, 2)
    closes = [line for line in seen if line["closed"]]
    # The first late notice closes the day again at once, with what there
    # is; the other chain's reopens it again only if it was still loading.
    checks.expect("the chains that came in after it reopen the day, and each reopening "
                  "closes it again, the last with both",
                  bool(closes) and all(line["reopened"] for line in closes)
                  and closes[-1]["settled"] == 2, str(seen))


def run(checks: Checks, stack: Stack) -> None:
    if not check_wiring(checks, stack):
        return
    token = owner_of_new_org(stack, "LGDC verify, cierre")
    other = owner_of_new_org(stack, "LGDC verify, cierre 2")
    days = free_days(stack, token)
    checks.expect("four days of the run's own, with no job yet", days is not None)
    if days is None:
        return
    first, second, upload_day, backup_day = days
    checks.note(f"days {', '.join(days)}")
    # A minute back, against clock skew between this machine and CloudWatch.
    since_ms = int((time.time() - 60) * 1000)
    if not check_last_chain(checks, stack, token, first, since_ms):
        return
    check_entries(checks, stack, token, other, second, since_ms)
    check_upload(checks, stack, token, upload_day, since_ms)
    check_backup(checks, stack, token, backup_day, since_ms)
