"""Precompute Lambda.

Computes the comparison of one followed branch at one radius for one day,
once, and stores it in DynamoDB for the tablero and Mis Precios to read
(`precomputed.py`). Its queue, lgdc-precompute, gets one entry per followed
branch and radius from the day close when a day closes or a merchant's
upload loads, and from the API when it answers a branch with no precompute
yet:

    {"feed_date": "2026-10-21", "company_id": 10, "banner_id": 1,
     "branch_id": "523", "radius_km": 5.0}

It runs the live query (`comparison.dashboard`) without a LIMIT, against the
read replica, or the primary when there is none, and writes the day as a new
generation: the rows, the summary and META, in that order. An entry for a day older than the one META
already names is stale: it computes nothing. Everything is repeatable: a
second run of an entry rewrites the same keys.

Each entry logs one line, `precompute {...}`, with what it did.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from typing import Any

import comparison
from common import SETTINGS, configure_logging, connect
from precomputed import ComparisonTable, FollowedBranch

configure_logging()
log = logging.getLogger("precompute")

# The replica's host, empty with read_replica_enabled = false.
REPLICA_HOST = os.getenv("DB_HOST_RO", "")
# How long an entry waits for the replica to replay what the primary had
# committed when the entry arrived. Past it, the entry fails and comes back.
CATCH_UP_SECONDS = 60

# The primary's position, and whether the replica has replayed up to one.
PRIMARY_LSN = "SELECT pg_current_wal_lsn()::text AS lsn"
REPLAYED = "SELECT pg_last_wal_replay_lsn() >= %s::pg_lsn AS replayed"

# One connection per host and execution environment, kept between
# invocations: straight to the instances, not through the Proxy. At most two
# invocations run at once (the event source mapping's maximum_concurrency).
_connections: dict[str, Any] = {}
_table: ComparisonTable | None = None


def database(host: str) -> Any:
    connection = _connections.get(host)
    if connection is None or connection.closed or connection.broken:
        connection = _connections[host] = connect(dataclasses.replace(SETTINGS, db_host=host))
    return connection


def table() -> ComparisonTable:
    global _table
    if _table is None:
        _table = ComparisonTable.from_env()
    return _table


def reader() -> Any:
    """Where to run the comparison: the replica once it has everything the
    primary had committed by now (the loads the entry is about), or the
    primary without a replica."""
    if not REPLICA_HOST:
        return database(SETTINGS.db_host)
    lsn = database(SETTINGS.db_host).execute(PRIMARY_LSN).fetchone()["lsn"]
    replica = database(REPLICA_HOST)
    deadline = time.monotonic() + CATCH_UP_SECONDS
    while not replica.execute(REPLAYED, (lsn,)).fetchone()["replayed"]:
        if time.monotonic() > deadline:
            raise RuntimeError(f"the replica has not replayed {lsn} after {CATCH_UP_SECONDS} s")
        time.sleep(1)
    return replica


def report(line: dict[str, Any]) -> dict[str, Any]:
    log.info("precompute %s", json.dumps(line))
    return line


def precompute(body: str) -> dict[str, Any]:
    feed_date, branch = FollowedBranch.from_entry(body)
    line: dict[str, Any] = {"feed_date": feed_date, **dataclasses.asdict(branch)}
    current = table().meta_day(branch)
    if current is not None and current > feed_date:
        return report({**line, "outcome": "stale", "meta": current})
    started = time.monotonic()
    result = comparison.dashboard(reader(), branch.company_id, branch.banner_id,
                                  branch.branch_id, feed_date, branch.radius_km, limit=None)
    queried = time.monotonic()
    published = table().publish(branch, feed_date, result["summary"], result["worst"])
    return report({**line, "outcome": "published" if published else "stale",
                   "rows": len(result["worst"]), "compared": result["summary"]["compared"],
                   "query_s": round(queried - started, 2),
                   "write_s": round(time.monotonic() - queried, 2)})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """A batch from the precompute queue: the entries that failed go back."""
    failures = []
    for record in event["Records"]:
        try:
            precompute(record["body"])
        except Exception:
            log.exception("entry %s failed: %s", record.get("messageId"),
                          record.get("body", "")[:200])
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
