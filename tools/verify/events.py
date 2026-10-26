"""The chain-processed notice (ticket 10).

Every chain a worker processes is announced once on the `cadena-procesada`
topic, which hands it to three subscriptions: the alerts queue (only `done`
with price changes), the day-close queue (every notice) and the admin's
email (only `failed`). Behind the alerts queue, the alert-matcher Lambda
turns the changes into alerts, which the portal shows.

What it establishes, through the stack's doors: the topic has the three
subscriptions with their filters, and the alert-matcher consumes the alerts
queue; a competitor's price change on a product a rule watches becomes one
alert in the portal, and on a product the rule does not watch, none; and
which notices reach which subscription. For that last part it subscribes its
own probe queues to the topic, each with the filter policy of one real
subscription, so SNS itself says what each would have received: the queues
behind the real ones are their consumers' (the day-close one, from ticket
11), and nobody can read the admin's inbox. A notice reaches a probe once,
even when the worker gets its job's message twice.

Its chains: a home branch and a rival 500 m away (899000031 and 899000032),
in the South Atlantic (-40, -45), more than 500 km from every other suite's
branches; and a chain whose file is broken (899000033), with no branch. The
home and the rival are loaded for yesterday and then for today, since a
price change is against the chain's previous day.
"""
from __future__ import annotations

import datetime
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from archives import (FEED_DATE, JOB_TIMEOUT, job, processed_after, put_raw, raw_key,
                      sepa_archive)
from cognito import owner_of_new_org
from harness import Checks, Stack, api, wait_for
from pipeline import jobs_queue_is_empty, s3_event

HOME, RIVAL, BROKEN = 899000031, 899000032, 899000033
LATITUDE, LONGITUDE = -40.0, -45.0
# On earthdistance's sphere 0.0045° of latitude is ~500 m.
RIVAL_NORTH = 0.0045
YESTERDAY = (datetime.date.fromisoformat(FEED_DATE) - datetime.timedelta(days=1)).isoformat()

EANS = [f"77999993{index:05d}" for index in range(3)]
BEFORE = dict.fromkeys(EANS, 100.0)
# Today the rival raises the first two products 20%; the rule watches only
# the first.
AFTER = {EANS[0]: 120.0, EANS[1]: 120.0, EANS[2]: 100.0}
WATCHED = EANS[0]

# The subscriptions the topic must have, by what each one delivers to.
ALERTS_FILTER = {"status": ["done"], "price_changes": [{"numeric": [">", 0]}]}
EMAIL_FILTER = {"status": ["failed"]}

# The day close rebuilds the neighbors once every chain of the day is in.
NEIGHBORS_TIMEOUT = 5 * 60
# From a job's end to its notice in a probe, or its alert in the portal.
NOTICE_TIMEOUT = 3 * 60
# How long a probe that must not get a notice is watched, after one that
# must has got it: SNS delivers to every subscription at once.
GRACE_SECONDS = 30


def notice(chain: int, status: str, price_changes: int = 0) -> dict[str, Any]:
    """The notice a chain's job of today should leave, as the topic gets it."""
    return {"feed_date": FEED_DATE, "company_id": chain, "kind": "sepa",
            "status": status, "price_changes": price_changes}


@dataclass
class Probe:
    """A queue of the run's own, subscribed to the topic with the filter
    policy of one real subscription: what SNS delivers to it is what that
    subscription gets."""
    name: str
    queue_url: str
    notices: list[dict[str, Any]] = field(default_factory=list)

    def drain(self, stack: Stack) -> None:
        while True:
            messages = stack.sqs.receive_message(
                QueueUrl=self.queue_url, MaxNumberOfMessages=10,
                WaitTimeSeconds=1).get("Messages", [])
            if not messages:
                return
            for message in messages:
                self.notices.append(json.loads(message["Body"]))
                stack.sqs.delete_message(QueueUrl=self.queue_url,
                                         ReceiptHandle=message["ReceiptHandle"])

    def about(self, stack: Stack, chain: int, feed_date: str = FEED_DATE) -> list[dict[str, Any]]:
        """Every notice it got for a chain's day."""
        self.drain(stack)
        return [notice for notice in self.notices
                if notice.get("company_id") == chain and notice.get("feed_date") == feed_date]


def subscription(stack: Stack, name: str) -> dict[str, str] | None:
    """The attributes of one of the topic's subscriptions (its endpoint and
    filter policy among them), by its name in Terraform; None if missing."""
    arn = stack.outputs.get("chain_processed_subscription_arns", {}).get(name)
    if not arn:
        return None
    return stack.sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"]


def filter_policy(subscription: dict[str, str] | None) -> Any:
    raw = (subscription or {}).get("FilterPolicy")
    return json.loads(raw) if raw else None


def subscribe_probe(stack: Stack, topic_arn: str, name: str, policy: Any) -> Probe:
    queue_url = stack.sqs.create_queue(
        QueueName=f"lgdc-verify-{name}-{uuid.uuid4().hex[:8]}",
        Attributes={"MessageRetentionPeriod": "3600"})["QueueUrl"]
    stack.queues.append(queue_url)
    queue_arn = stack.sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    stack.sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"},
            "Action": "sqs:SendMessage", "Resource": queue_arn,
            "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
        }],
    })})
    attributes = {"RawMessageDelivery": "true"}
    if policy is not None:
        attributes["FilterPolicy"] = json.dumps(policy)
    subscription = stack.sns.subscribe(
        TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn, Attributes=attributes,
        ReturnSubscriptionArn=True)["SubscriptionArn"]
    stack.subscriptions.append(subscription)
    return Probe(name, queue_url)


def check_wiring(checks: Checks, stack: Stack) -> dict[str, Probe] | None:
    checks.section("Notices · the topic, its three subscriptions and the alert-matcher")
    topic_arn = stack.outputs.get("chain_processed_topic_arn")
    checks.expect("the stack has the cadena-procesada topic", bool(topic_arn),
                  "no chain_processed_topic_arn output")
    if not topic_arn:
        return None
    alerts = subscription(stack, "alerts")
    checks.expect("the alerts queue gets only done notices with price changes",
                  alerts is not None
                  and alerts["Endpoint"] == stack.outputs.get("alerts_queue_arn")
                  and filter_policy(alerts) == ALERTS_FILTER
                  and alerts.get("RawMessageDelivery") == "true", str(alerts))
    close = subscription(stack, "day_close")
    checks.expect("the day-close queue gets every notice, unfiltered",
                  close is not None
                  and close["Endpoint"] == stack.outputs.get("day_close_queue_arn")
                  and filter_policy(close) is None
                  and close.get("RawMessageDelivery") == "true", str(close))
    email = subscription(stack, "admin_email")
    checks.expect("the admin's email gets only failed notices",
                  email is not None and email["Protocol"] == "email"
                  and filter_policy(email) == EMAIL_FILTER, str(email))
    admin = os.getenv("BOOTSTRAP_EMAIL")
    if email is not None and admin:
        checks.expect("at BOOTSTRAP_EMAIL", email["Endpoint"] == admin, email["Endpoint"])
    if email is not None:
        pending = email.get("PendingConfirmation") == "true"
        checks.note(f"the email subscription is {'pending its confirmation' if pending else 'confirmed'}")

    function = stack.outputs.get("alert_matcher_function_name", "")
    mappings = stack.lambda_.list_event_source_mappings(FunctionName=function)[
        "EventSourceMappings"] if function else []
    checks.expect("the alert-matcher Lambda consumes the alerts queue",
                  any(mapping["EventSourceArn"] == stack.outputs.get("alerts_queue_arn")
                      and mapping["State"] == "Enabled" for mapping in mappings), str(mappings))

    return {
        "alerts": subscribe_probe(stack, topic_arn, "alerts", ALERTS_FILTER),
        "close": subscribe_probe(stack, topic_arn, "close", None),
        "email": subscribe_probe(stack, topic_arn, "email", EMAIL_FILTER),
    }


def load(stack: Stack, token: str, chain: int, prices: dict[str, float], feed_date: str,
         north: float = 0.0) -> dict[str, Any] | None:
    previous = job(stack, token, chain, feed_date)
    put_raw(stack, chain, "verify.zip", sepa_archive(
        chain, prices, latitude=LATITUDE + north, longitude=LONGITUDE), feed_date)
    return wait_for(stack, processed_after(stack, token, chain, previous, "done", feed_date),
                    JOB_TIMEOUT)


def rival_is_neighbor(stack: Stack, token: str) -> bool:
    page = api(stack, "GET", f"/branches/{HOME}/1/1/competitors?radius_km=10",
               token).json() or {}
    return page.get("total") == 1


def set_up(checks: Checks, stack: Stack) -> tuple[str, int] | None:
    checks.section("Alerts · a home branch, its rival 500 m away and a rule")
    token = owner_of_new_org(stack, "LGDC verify, alertas")
    loaded = [load(stack, token, HOME, BEFORE, YESTERDAY),
              load(stack, token, RIVAL, BEFORE, YESTERDAY, RIVAL_NORTH)]
    checks.expect("yesterday's jobs of both chains end in done", all(loaded),
                  str([job(stack, token, chain, YESTERDAY) for chain in (HOME, RIVAL)]))
    if not all(loaded):
        return None
    followed = api(stack, "POST", "/me/branches", token,
                   {"company_id": HOME, "banner_id": 1, "branch_id": "1", "radius_km": 10})
    rule = api(stack, "POST", "/rules", token, {
        "name": "LGDC verify, alertas", "company_id": HOME, "banner_id": 1, "branch_id": "1",
        "products": [WATCHED]})
    rule_id = (rule.json() or {}).get("rule_id")
    checks.expect("the org follows the home branch and watches one product on it",
                  followed.status == 201 and rule.status == 201 and bool(rule_id),
                  f"{followed.status} {rule.status} {rule.body[:200]}")
    neighbors = wait_for(stack, lambda: rival_is_neighbor(stack, token), NEIGHBORS_TIMEOUT)
    checks.expect("the rival is in the home branch's competitive set", bool(neighbors))
    return (token, rule_id) if rule_id and neighbors else None


def alerts_of(stack: Stack, token: str, rule_id: int) -> list[dict[str, Any]]:
    listed = api(stack, "GET", "/alerts?limit=100", token).json() or []
    return [alert for alert in listed if alert.get("rule_id") == rule_id]


def check_alert(checks: Checks, stack: Stack, probes: dict[str, Probe], token: str,
                rule_id: int) -> bool:
    checks.section("Alerts · a rival's price change on a watched product")
    done = load(stack, token, RIVAL, AFTER, FEED_DATE, RIVAL_NORTH)
    checks.expect("today's job of the rival ends in done", done is not None,
                  str(job(stack, token, RIVAL)))
    if done is None:
        return False
    expected = notice(RIVAL, "done", 2)
    for name in ("alerts", "close"):
        got = wait_for(stack, lambda probe=probes[name]: probe.about(stack, RIVAL),
                       NOTICE_TIMEOUT, interval=5)
        checks.expect(f"the {name} subscription gets its notice: done, with 2 price changes",
                      got == [expected], str(got))

    alerts = wait_for(stack, lambda: alerts_of(stack, token, rule_id), NOTICE_TIMEOUT,
                      interval=5) or []
    time.sleep(GRACE_SECONDS)
    alerts = alerts_of(stack, token, rule_id)
    checks.expect("the portal shows one alert, for the watched product only",
                  [alert.get("ean") for alert in alerts] == [WATCHED], str(alerts))
    if alerts:
        alert = alerts[0]
        checks.expect("from the rival, from 100.00 to 120.00, +20 %",
                      alert.get("company_id") == RIVAL and alert.get("read_at") is None
                      and (float(alert["previous_price"]), float(alert["price"]),
                           float(alert["delta_pct"])) == (100, 120, 20), str(alert))
    unread = (api(stack, "GET", "/alerts/unread-count", token).json() or {}).get("count")
    checks.expect("and counts it as unread", unread == len(alerts), str(unread))
    checks.expect("the admin's email subscription gets nothing",
                  probes["email"].about(stack, RIVAL) == [])
    return True


def check_once(checks: Checks, stack: Stack, probes: dict[str, Probe]) -> None:
    checks.section("Notices · once per chain, even when its job's message comes twice")
    key = raw_key(RIVAL, "verify.zip")
    version = stack.s3.head_object(Bucket=stack.bucket, Key=key)["VersionId"]
    stack.sqs.send_message(QueueUrl=stack.outputs["jobs_queue_url"],
                           MessageBody=s3_event(stack.bucket, key, version))
    drained = wait_for(stack, lambda: jobs_queue_is_empty(stack), JOB_TIMEOUT)
    checks.expect("a worker consumed the copy", bool(drained))
    time.sleep(GRACE_SECONDS)
    for name in ("alerts", "close"):
        got = probes[name].about(stack, RIVAL)
        checks.expect(f"the {name} subscription still has one notice of the rival's day",
                      len(got) == 1, str(got))


def check_no_changes(checks: Checks, stack: Stack, probes: dict[str, Probe],
                     token: str) -> None:
    checks.section("Notices · a chain with no price changes")
    done = load(stack, token, HOME, BEFORE, FEED_DATE)
    checks.expect("today's job of the home branch, same prices, ends in done",
                  done is not None, str(job(stack, token, HOME)))
    if done is None:
        return
    got = wait_for(stack, lambda: probes["close"].about(stack, HOME), NOTICE_TIMEOUT,
                   interval=5)
    checks.expect("the day-close subscription gets it: done, with 0 price changes",
                  got == [notice(HOME, "done")], str(got))
    time.sleep(GRACE_SECONDS)
    checks.expect("the alerts subscription does not",
                  probes["alerts"].about(stack, HOME) == [])
    checks.expect("nor the admin's email", probes["email"].about(stack, HOME) == [])


def check_failure(checks: Checks, stack: Stack, probes: dict[str, Probe], token: str) -> None:
    checks.section("Notices · a chain that fails emails the admin")
    previous = job(stack, token, BROKEN)
    put_raw(stack, BROKEN, "broken.zip", b"this is not a zip")
    failed = wait_for(stack, processed_after(stack, token, BROKEN, previous, "failed"),
                      JOB_TIMEOUT)
    checks.expect("the broken file's job ends in failed", failed is not None,
                  str(job(stack, token, BROKEN)))
    if failed is None:
        return
    expected = [notice(BROKEN, "failed")]
    for name in ("email", "close"):
        got = wait_for(stack, lambda probe=probes[name]: probe.about(stack, BROKEN),
                       NOTICE_TIMEOUT, interval=5)
        checks.expect(f"the {'admin' if name == 'email' else 'day-close'}'s subscription "
                      f"gets its notice: failed", got == expected, str(got))
    time.sleep(GRACE_SECONDS)
    checks.expect("the alerts subscription does not",
                  probes["alerts"].about(stack, BROKEN) == [])


def run(checks: Checks, stack: Stack) -> None:
    probes = check_wiring(checks, stack)
    if probes is None:
        return
    ready = set_up(checks, stack)
    if ready is None:
        return
    token, rule_id = ready
    if check_alert(checks, stack, probes, token, rule_id):
        check_once(checks, stack, probes)
    check_no_changes(checks, stack, probes, token)
    check_failure(checks, stack, probes, token)
