"""Where the tablero and Mis Precios read from: the precompute, or Postgres.

A followed branch at its configured radius reads the precompute in DynamoDB
(`precomputed.py`): the day META names, always a complete one. The live
query in Postgres answers what the precompute does not have:

- **Another radius** than the configured one: it is free, and only the
  configured radius is precomputed (docs/adr/0005).
- **Another day** than the one META names, asked for by `feed_date`.
- **No META** for the branch at its radius (a branch followed today, a day
  whose rows expired): the API also queues its precompute, once every few
  minutes at most.

Without `feed_date`, the live answer is for META's day too, when there is
one: the screens show a complete day, whichever radius. Only without any
falls it back to the latest day a chain loaded.

Every answer says which one it came from (`source`: `precomputed` or
`live`, or `pending`). A live query is cut at LIVE_TIMEOUT, under API
Gateway's 30 s: a dense radius (hundreds of neighbours in CABA) takes longer.
Cut for a branch whose precompute was just queued, the answer is `pending`
and the portal asks again in a few seconds; at another radius it is a 503
that says why. Mis Precios pages by an opaque cursor, which remembers the day and
where the page came from: the next page reads the same write of the
precompute, or the same day live, even if META moved in between.

DynamoDB or SQS failing does not fail a read: it answers live, and logs.
The one exception is the next page of a precomputed walk filtered by
description, which has no live equivalent (503).
"""
from __future__ import annotations

import base64
import binascii
import datetime
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi import HTTPException

import comparison
import precomputed
import queries
from common import BotoCoreError, ClientError, aws_client
from precomputed import ComparisonTable, CompleteDay, FollowedBranch, Page

log = logging.getLogger("api")

PRECOMPUTE_QUEUE_URL = os.getenv("PRECOMPUTE_QUEUE_URL", "")
# Where an answer came from. `pending`: the live query took too long and the
# precompute is on its way.
PRECOMPUTED, LIVE, PENDING = "precomputed", "live", "pending"
# What a live comparison may take, well under API Gateway's 30 s.
LIVE_TIMEOUT = "20s"
TOO_SLOW = "comparison_too_slow"
AWS_ERRORS = (BotoCoreError, ClientError)

_table: ComparisonTable | None = None
_sqs: Any = None


def table() -> ComparisonTable:
    global _table
    if _table is None:
        _table = ComparisonTable.from_env()
    return _table


def queue_precompute(feed_date: str, branches: list[FollowedBranch]) -> None:
    """Queue these comparisons, as the day close does. Best effort: a read
    that could not queue still answers."""
    global _sqs
    try:
        _sqs = _sqs or aws_client("sqs")
        precomputed.queue(_sqs, PRECOMPUTE_QUEUE_URL, feed_date, branches)
    except (*AWS_ERRORS, RuntimeError) as error:
        log.warning("could not queue the precompute of %s for %s: %s",
                    [branch.key for branch in branches], feed_date, error)


@dataclass(frozen=True)
class Read:
    """What a request asks for, and where it is answered from."""
    # The followed branch at its configured radius: the partition.
    branch: FollowedBranch
    # The radius the answer is for.
    radius_km: float
    # What META says, or None (no META, its rows expired, or DynamoDB failed).
    complete: CompleteDay | None
    # Whether the answer comes from the precompute (else live).
    precomputed: bool
    # No complete day at all: answering live also queues the precompute.
    missing: bool

    def live_day(self, connection: Any, feed_date: str | None) -> str | None:
        """The day a live answer is for: the one asked for, META's, or the
        latest a chain loaded."""
        if feed_date:
            return feed_date
        if self.complete is not None:
            return self.complete.feed_date
        return queries.latest_feed_date(connection)


def plan(org_branch: dict[str, Any], company_id: int, banner_id: int, branch_id: str,
         feed_date: str | None, radius_km: float | None, max_radius_km: float) -> Read:
    configured = float(org_branch["radius_km"])
    requested = radius_km or configured
    branch = FollowedBranch(company_id, banner_id, branch_id, configured)
    radius = min(requested, max_radius_km)
    try:
        complete = table().complete_day(branch)
    except AWS_ERRORS as error:
        log.warning("could not read META of %s, answering live: %s", branch.key, error)
        return Read(branch, radius, None, False, False)
    precomputed_answer = (complete is not None and requested == configured
                          and feed_date in (None, complete.feed_date))
    return Read(branch, radius, complete, precomputed_answer, complete is None)


def queue_if_missing(read: Read, feed_date: str) -> None:
    if not read.missing:
        return
    try:
        if table().claim_queueing(read.branch):
            queue_precompute(feed_date, [read.branch])
    except AWS_ERRORS as error:
        log.warning("could not claim the queueing of %s: %s", read.branch.key, error)


def bounded(connection: Any, query: Any) -> Any:
    """Run a live comparison under LIVE_TIMEOUT. None if it was cut."""
    try:
        with connection.transaction():
            connection.execute(f"SET LOCAL statement_timeout = '{LIVE_TIMEOUT}'")
            return query()
    except psycopg.errors.QueryCanceled:
        return None


def precomputed_rows(read: Read, feed_date: str, generation: str, after: int, limit: int,
                     q: str | None = None) -> Page | None:
    """A page of the precompute, or None if DynamoDB failed."""
    try:
        return table().page(read.branch, feed_date, generation, after, limit, q)
    except AWS_ERRORS as error:
        log.warning("could not read %s's %s, answering live: %s", read.branch.key, feed_date,
                    error)
        return None


# -- The cursor -------------------------------------------------------------------

def encode_cursor(feed_date: str, **position: Any) -> str:
    """`generation` and `after` (a rank) for a precomputed page, `offset`
    for a live one."""
    raw = json.dumps({"feed_date": feed_date, **position}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        datetime.date.fromisoformat(decoded["feed_date"])
        if "offset" in decoded:
            valid = set(decoded) == {"feed_date", "offset"} and _position(decoded["offset"])
        else:
            valid = (set(decoded) == {"feed_date", "generation", "after"}
                     and _position(decoded["after"])
                     and isinstance(decoded["generation"], str)
                     and decoded["generation"].isdigit())
        if not valid:
            raise ValueError(cursor)
        return decoded
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError,
            AttributeError):
        raise HTTPException(400, "invalid cursor") from None


def _position(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# -- The two screens --------------------------------------------------------------

def dashboard(connection: Any, read: Read, feed_date: str | None,
              limit: int) -> dict[str, Any]:
    """The tablero: the position summary and the worst `limit` rows."""
    if read.precomputed and read.complete is not None:
        complete = read.complete
        found = precomputed_rows(read, complete.feed_date, complete.generation, 0, limit)
        if found is not None:
            return {"feed_date": complete.feed_date, "source": PRECOMPUTED,
                    "summary": complete.summary, "worst": found.rows}
    resolved = read.live_day(connection, feed_date)
    if resolved is None:
        # No ingestion has ever completed. Same shape, so the screen renders
        # its "todavia no hay ingestas" state instead of handling a null body.
        return {"feed_date": None, "source": LIVE, "summary": comparison.empty_summary(),
                "worst": []}
    queue_if_missing(read, resolved)
    answer = bounded(connection, lambda: comparison.dashboard(
        connection, read.branch.company_id, read.branch.banner_id, read.branch.branch_id,
        resolved, read.radius_km, limit))
    if answer is None:
        return cut(read, {"feed_date": resolved, "summary": comparison.empty_summary(),
                          "worst": []})
    return {"feed_date": resolved, "source": LIVE, **answer}


def cut(read: Read, empty: dict[str, Any]) -> dict[str, Any]:
    """A live query that took too long: `pending` if the precompute of that
    radius is on its way, a 503 at a radius nothing precomputes."""
    if read.missing and read.radius_km == read.branch.radius_km:
        return {**empty, "source": PENDING}
    raise HTTPException(503, TOO_SLOW)


def comparison_page(connection: Any, read: Read, feed_date: str | None, q: str | None,
                    limit: int, cursor: str | None) -> dict[str, Any]:
    """A page of Mis Precios, worst gap first, and the cursor of the next."""
    position = decode_cursor(cursor) if cursor else None
    if position is not None and "generation" in position:
        return next_precomputed_page(connection, read, position, q, limit)
    if position is None and read.precomputed and read.complete is not None:
        complete = read.complete
        found = precomputed_rows(read, complete.feed_date, complete.generation, 0, limit, q)
        if found is not None:
            return precomputed_page(complete.feed_date, complete.generation, found)

    if position is not None:
        return live_page(connection, read, position["feed_date"], q, limit, position["offset"])
    resolved = read.live_day(connection, feed_date)
    if resolved is None:
        return {"feed_date": None, "source": LIVE, "items": [], "next_cursor": None}
    queue_if_missing(read, resolved)
    return live_page(connection, read, resolved, q, limit, 0)


def next_precomputed_page(connection: Any, read: Read, position: dict[str, Any],
                          q: str | None, limit: int) -> dict[str, Any]:
    """The page after a precomputed one: the same write, from the next rank."""
    if read.radius_km != read.branch.radius_km:
        raise HTTPException(400, "the cursor is for the configured radius")
    day, generation, after = position["feed_date"], position["generation"], position["after"]
    found = precomputed_rows(read, day, generation, after, limit, q)
    if found is not None:
        return precomputed_page(day, generation, found)
    if q:
        raise HTTPException(503, "the precompute is not available; start over")
    # Unfiltered, a rank is an offset: the live query sorts the same way.
    return live_page(connection, read, day, q, limit, after)


def precomputed_page(day: str, generation: str, found: Page) -> dict[str, Any]:
    return {"feed_date": day, "source": PRECOMPUTED, "items": found.rows,
            "next_cursor": (encode_cursor(day, generation=generation, after=found.next_after)
                            if found.next_after is not None else None)}


def live_page(connection: Any, read: Read, day: str, q: str | None, limit: int,
              offset: int) -> dict[str, Any]:
    # One row past the page says whether there is a next one.
    rows = bounded(connection, lambda: comparison.page(
        connection, read.branch.company_id, read.branch.banner_id, read.branch.branch_id,
        day, read.radius_km, q, limit + 1, offset))
    if rows is None:
        return cut(read, {"feed_date": day, "items": [], "next_cursor": None})
    return {"feed_date": day, "source": LIVE, "items": rows[:limit],
            "next_cursor": (encode_cursor(day, offset=offset + limit)
                            if len(rows) > limit else None)}
