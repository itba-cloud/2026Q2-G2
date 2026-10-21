"""Day-close Lambda.

Closes each day once its last chain is in: runs the catalogue maintenance
(`refresh_neighbors` and `normalize_catalog`) and queues one precompute entry
per followed branch and radius. Three things invoke it:

- **The day-close queue**, where the cadena-procesada topic leaves every
  notice. A SEPA chain's notice marks the chain in its day (`feed_day_chain`)
  and tries to close the day: the close is won by the one invocation whose
  `UPDATE feed_day ... WHERE closed_at IS NULL` finds every chain the
  ingestion stored in raw/ for that day with a settled job (done or failed).
  A merchant's upload closes nothing: it queues its merchant's branch alone.
- **The API**, for `POST /admin/neighbors/refresh`: the rebuild of the
  neighbors and the precompute of every followed branch, asynchronously.
- **A daily cron**, the backup: a chain whose job ended in the DLQ never
  sends a notice, so at 10:00 in Buenos Aires it closes the day of that date
  with what there is.

A chain whose notice arrives after its day closed (one the cron did not wait
for) reopens the day and closes it again at once, with what there is: the
cron already gave up waiting for the rest.

Each notice and each cron invocation logs one line, `day-close {...}`, with
what it decided; the cron's invocation also answers with it.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import os
import re
from typing import Any

import maintenance
from common import SETTINGS, aws_client, configure_logging, connect
from precomputed import FollowedBranch, followed_branches, queue

configure_logging()
log = logging.getLogger("close")

PRECOMPUTE_QUEUE_URL = os.getenv("PRECOMPUTE_QUEUE_URL", "")
# The cron's day is the date in Buenos Aires, which has had no daylight
# saving since 2009.
BUENOS_AIRES = datetime.timezone(datetime.timedelta(hours=-3))
# Where the ingestion stores a chain's file for a day, the key the worker
# makes its job from (app/batch/worker/main.py, RAW_KEY).
RAW_KEY = re.compile(r"^raw/feed_date=\d{4}-\d{2}-\d{2}/chain_id=(\d+)/[^/]+$")

# Marks a chain in its day, under the day's lock: a close in progress
# finishes first, and the mark sees whether it took the chain in. A chain
# the day did not have yet reopens a closed day.
ADD_DAY = "INSERT INTO feed_day (feed_date) VALUES (%s) ON CONFLICT DO NOTHING"

LOCK_DAY = "SELECT closed_at FROM feed_day WHERE feed_date = %s FOR UPDATE"

MARK_CHAIN = """
    INSERT INTO feed_day_chain (feed_date, chain_id) VALUES (%s, %s)
    ON CONFLICT DO NOTHING
    RETURNING chain_id
"""

REOPEN = """
    UPDATE feed_day SET closed_at = NULL
    WHERE feed_date = %s AND closed_at IS NOT NULL
    RETURNING feed_date
"""

# Of the chains stored in raw/ for the day, those whose job settled.
SETTLED = """
    SELECT chain_id FROM ingest_job
    WHERE kind = 'sepa' AND feed_date = %s AND chain_id = ANY(%s)
      AND status IN ('done', 'failed')
"""

# Exactly one invocation wins: the others find closed_at set once the
# winner commits, or a chain still loading. A forced close (the cron, or a
# late chain) does not wait for the chains still loading.
CLOSE = """
    UPDATE feed_day SET closed_at = now()
    WHERE feed_date = %(feed_date)s AND closed_at IS NULL
      AND (%(forced)s OR NOT EXISTS (
            SELECT 1 FROM unnest(%(stored)s::text[]) AS stored (chain_id)
            WHERE stored.chain_id <> ALL(%(settled)s::text[])))
    RETURNING feed_date
"""

# What the close took in: a chain marked here does not reopen the day when
# its notice comes.
MARK_SETTLED = """
    INSERT INTO feed_day_chain (feed_date, chain_id)
    SELECT %s, unnest(%s::text[])
    ON CONFLICT DO NOTHING
"""

# One connection per execution environment, kept between invocations.
_connection: Any = None
_clients: dict[str, Any] = {}


def database() -> Any:
    global _connection
    if _connection is None or _connection.closed or _connection.broken:
        _connection = connect()
    return _connection


def client(service: str) -> Any:
    if service not in _clients:
        _clients[service] = aws_client(service)
    return _clients[service]


def stored_chains(feed_date: str) -> list[str]:
    """The chains the ingestion stored in raw/ for the day: each is a job."""
    chains: set[str] = set()
    pages = client("s3").get_paginator("list_objects_v2").paginate(
        Bucket=SETTINGS.bucket, Prefix=f"raw/feed_date={feed_date}/")
    for page in pages:
        for item in page.get("Contents", []):
            if match := RAW_KEY.match(item["Key"]):
                chains.add(match.group(1))
    return sorted(chains, key=int)


def queue_precompute(feed_date: str, entries: list[FollowedBranch]) -> None:
    queue(client("sqs"), PRECOMPUTE_QUEUE_URL, feed_date, entries)


def reopened_by(connection: Any, feed_date: str, chain_id: str) -> bool:
    """Mark a chain in its day. Whether the day was closed without it, and
    is open again."""
    with connection.transaction():
        connection.execute(ADD_DAY, (feed_date,))
        closed_at = connection.execute(LOCK_DAY, (feed_date,)).fetchone()["closed_at"]
        new = connection.execute(MARK_CHAIN, (feed_date, chain_id)).fetchone() is not None
        if not new or closed_at is None:
            return False
        return connection.execute(REOPEN, (feed_date,)).fetchone() is not None


def close(connection: Any, feed_date: str, forced: bool = False) -> dict[str, Any]:
    """Try to close the day: once every stored chain settled or, forced,
    with what there is. The winner runs the maintenance and queues the
    precompute in the same transaction, so a close that fails halfway leaves
    the day open. The TRUNCATE in refresh_neighbors locks branch_neighbor
    until the commit, so it goes last but for the queueing."""
    stored = stored_chains(feed_date)
    result: dict[str, Any] = {"stored": len(stored), "settled": 0, "closed": False,
                              "precompute": []}
    # A day with nothing in raw/ has nothing to close: no ingestion, or only
    # a job whose file is gone.
    if not stored:
        return result
    with connection.transaction():
        connection.execute(ADD_DAY, (feed_date,))
        settled = [row["chain_id"] for row in
                   connection.execute(SETTLED, (feed_date, stored)).fetchall()]
        result["settled"] = len(settled)
        won = connection.execute(CLOSE, {"feed_date": feed_date, "forced": forced,
                                         "stored": stored, "settled": settled}).fetchone()
        if won is None:
            return result
        connection.execute(MARK_SETTLED, (feed_date, settled))
        products = maintenance.normalize_catalog(connection)
        entries = followed_branches(connection)
        pairs = maintenance.refresh_neighbors(connection)
        # The day's prices are new to the planner: autovacuum analyzes a
        # partition only once ~10% of it changed, which a day of a month
        # never is. Without fresh statistics it takes the new day for one
        # row and the comparison goes from seconds to past the precompute's
        # timeout (measured: 4.5 s -> over 5 min).
        connection.execute("ANALYZE price")
        queue_precompute(feed_date, entries)
    log.info("day %s closed: %d products normalized, %d neighbor pairs, %d precompute "
             "entries", feed_date, products, pairs, len(entries))
    result.update(closed=True, precompute=[dataclasses.asdict(entry) for entry in entries])
    return result


def report(line: dict[str, Any]) -> dict[str, Any]:
    log.info("day-close %s", json.dumps(line))
    return line


def on_notice(notice: dict[str, Any]) -> dict[str, Any]:
    feed_date, company_id = str(notice["feed_date"]), int(notice["company_id"])
    line: dict[str, Any] = {"trigger": "notice", "feed_date": feed_date,
                            "kind": notice["kind"], "company_id": company_id,
                            "status": notice["status"]}
    connection = database()
    if notice["kind"] == "upload":
        # A merchant's list is its own branch's only: it does not wait for
        # the day, and the day does not wait for it.
        entries = followed_branches(connection, company_id) if notice["status"] == "done" else []
        queue_precompute(feed_date, entries)
        return report({**line, "reopened": False, "closed": False,
                       "precompute": [dataclasses.asdict(entry) for entry in entries]})
    reopened = reopened_by(connection, feed_date, str(company_id))
    return report({**line, "reopened": reopened,
                   **close(connection, feed_date, forced=reopened)})


def on_schedule(event: dict[str, Any]) -> dict[str, Any]:
    """The backup: the day of the cron's time (or now), in Buenos Aires."""
    at = (datetime.datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
          if "time" in event else datetime.datetime.now(datetime.timezone.utc))
    feed_date = at.astimezone(BUENOS_AIRES).date().isoformat()
    return report({"trigger": "backup", "feed_date": feed_date,
                   **close(database(), feed_date, forced=True)})


# The last day that closed: the one the precompute shows.
LAST_CLOSED_DAY = """
    SELECT max(feed_date)::text AS day FROM feed_day WHERE closed_at IS NOT NULL
"""


def on_refresh_neighbors() -> dict[str, Any]:
    """POST /admin/neighbors/refresh, asynchronously: the whole rebuild takes
    longer than the API may wait. Every followed branch is precomputed
    again for the last closed day, with the new pairs."""
    connection = database()
    with connection.transaction():
        pairs = maintenance.refresh_neighbors(connection)
        entries = followed_branches(connection)
        day = connection.execute(LAST_CLOSED_DAY).fetchone()["day"]
        if day is not None:
            queue_precompute(day, entries)
    return report({"trigger": "admin", "pairs": pairs, "feed_date": day,
                   "precompute": len(entries) if day is not None else 0})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """A batch from the day-close queue, the cron's event, or the API's
    rebuild of the neighbors."""
    if event.get("action") == "refresh_neighbors":
        return on_refresh_neighbors()
    if "Records" not in event:
        return on_schedule(event)
    failures = []
    for record in event["Records"]:
        try:
            on_notice(json.loads(record["body"]))
        except Exception:
            log.exception("notice %s failed: %s", record.get("messageId"),
                          record.get("body", "")[:200])
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
