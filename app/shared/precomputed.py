"""The precompute: what it computes, how it is queued, and where it is stored.

The precompute computes the comparison of each followed branch at a radius
once a day, and stores it in the DynamoDB table lgdc-comparison for the
tablero and Mis Precios. Everything about it that more than one component
needs lives here: the day close and the API queue entries
(`followed_branches`, `queue`), the precompute Lambda (app/batch/precompute)
writes the table and the API reads it, both through `ComparisonTable`. One
partition per followed branch and radius:

    PK  CMP#<company>:<banner>:<branch>#<radius_km>
    SK  META                            the day that is complete to show
        SUM#<feed_date>                 a day's position summary (history)
        ROW#<feed_date>#<generation>#R00001   a write's rows, worst gap first
        QUEUED                          the API queued this partition

Each write of a day is a new generation (the time it was written): its rows
first, then the day's summary, and META last, which names the day and the
generation and carries the summary. A reader reads META and then that
generation's rows, so it never sees a write half done: not a new day, and
not the same day written again (a merchant's upload, a day reopened). META
only moves forward: an entry for a day older than the one META names is
stale, and the conditional write refuses it if two race.

`expires_at` (epoch seconds) is the table's TTL: the rows go a week after
they are written, the summaries after 400 days (the TP4's Tendencias reads
them). META has none: it is never deleted, so it always knows the newest day,
and an old entry can never take its place. It says until when its rows last
(`rows_expire_at`); past that the day is not shown, and the API answers live.

    from precomputed import ComparisonTable, FollowedBranch
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, NamedTuple

import boto3
from boto3.dynamodb.conditions import Attr, Key

from comparison import ROW_COLUMNS, SUMMARY_COLUMNS
from common import SETTINGS

DAY_SECONDS = 24 * 60 * 60
ROWS_TTL_SECONDS = 7 * DAY_SECONDS
SUMMARY_TTL_SECONDS = 400 * DAY_SECONDS
# How long the API waits before queueing a partition's precompute again: a
# tablero reloaded while the first one is on its way queues nothing.
QUEUED_TTL_SECONDS = 5 * 60
# The rank's width in the sort key: R00001 up to R99999.
MAX_ROWS = 99_999
# Prices stay the strings Postgres' NUMERIC gives, as the live query's do:
# DynamoDB's numbers would drop the trailing zeros ("100.00" -> 100).
DECIMAL_COLUMNS = ("own_price", "min_price", "avg_price", "max_price", "pct_over_min")
# The most messages SendMessageBatch takes at once.
SQS_BATCH_SIZE = 10


@dataclass(frozen=True)
class FollowedBranch:
    """A branch some org follows, at the radius it follows it: one comparison
    to precompute, and one partition of the table."""
    company_id: int
    banner_id: int
    branch_id: str
    radius_km: float

    @property
    def key(self) -> str:
        return (f"CMP#{self.company_id}:{self.banner_id}:{self.branch_id}"
                f"#{float(self.radius_km):.1f}")

    def entry(self, feed_date: str) -> str:
        """The precompute queue's message for this branch and a day."""
        return json.dumps({"feed_date": feed_date, **asdict(self)})

    @classmethod
    def from_entry(cls, body: str) -> tuple[str, FollowedBranch]:
        entry = json.loads(body)
        return str(entry["feed_date"]), cls(int(entry["company_id"]), int(entry["banner_id"]),
                                            str(entry["branch_id"]), float(entry["radius_km"]))


# Every followed branch and radius, or (with a company) only that company's.
FOLLOWED = """
    SELECT DISTINCT company_id, banner_id, branch_id, radius_km FROM org_branch
    WHERE %(company_id)s::int IS NULL OR company_id = %(company_id)s
    ORDER BY company_id, banner_id, branch_id, radius_km
"""


def followed_branches(connection: Any, company_id: int | None = None) -> list[FollowedBranch]:
    rows = connection.execute(FOLLOWED, {"company_id": company_id}).fetchall()
    return [FollowedBranch(row["company_id"], row["banner_id"], row["branch_id"],
                           float(row["radius_km"])) for row in rows]


def queue(sqs: Any, queue_url: str, feed_date: str, branches: list[FollowedBranch]) -> None:
    """Queue the precompute of these branches for a day, in batches of 10.
    Raises if SQS refuses any."""
    messages = [{"Id": str(index), "MessageBody": branch.entry(feed_date)}
                for index, branch in enumerate(branches)]
    for start in range(0, len(messages), SQS_BATCH_SIZE):
        sent = sqs.send_message_batch(QueueUrl=queue_url,
                                      Entries=messages[start:start + SQS_BATCH_SIZE])
        if sent.get("Failed"):
            raise RuntimeError(f"the precompute queue refused {sent['Failed']}")


class CompleteDay(NamedTuple):
    """What META says: the day to show, the write whose rows to read, and
    its position summary."""
    feed_date: str
    generation: str
    summary: dict[str, int]


class Page(NamedTuple):
    """A page of a write's rows, and the rank of the page's last row when
    there is a next page."""
    rows: list[dict[str, Any]]
    next_after: int | None


def row_key(feed_date: str, generation: str, rank: int) -> str:
    return f"ROW#{feed_date}#{generation}#R{rank:05d}"


def _encode_row(row: dict[str, Any]) -> dict[str, Any]:
    item = {column: row[column] for column in ROW_COLUMNS}
    for column in DECIMAL_COLUMNS:
        if item[column] is not None:
            item[column] = str(item[column])
    item["offers"] = int(item["offers"])
    # What the description filter looks in: contains() is case-sensitive.
    item["desc_norm"] = (row["description"] or "").casefold()
    return item


def _decode_row(item: dict[str, Any]) -> dict[str, Any]:
    row = {column: item.get(column) for column in ROW_COLUMNS}
    row["offers"] = int(row["offers"])
    return row


def _summary(item: dict[str, Any]) -> dict[str, int]:
    return {column: int(item[column]) for column in SUMMARY_COLUMNS}


class ComparisonTable:
    def __init__(self, table: Any) -> None:
        self.table = table

    @classmethod
    def from_env(cls) -> ComparisonTable:
        resource = boto3.resource("dynamodb", region_name=SETTINGS.region,
                                  endpoint_url=SETTINGS.endpoint_url)
        return cls(resource.Table(os.environ["COMPARISON_TABLE"]))

    def _meta(self, branch: FollowedBranch) -> dict[str, Any] | None:
        return self.table.get_item(Key={"PK": branch.key, "SK": "META"}).get("Item")

    # -- The precompute's side ------------------------------------------------

    def meta_day(self, branch: FollowedBranch) -> str | None:
        """The day META names, its rows still there or not: what a new day
        has to be newer than (or equal to, to write it again)."""
        item = self._meta(branch)
        return item["feed_date"] if item else None

    def publish(self, branch: FollowedBranch, feed_date: str, summary: dict[str, Any],
                rows: list[dict[str, Any]]) -> bool:
        """Write a day as a new generation: its rows, its summary, and META
        last. False if META already names a newer day: the rows and summary
        are written all the same (they expire), but nobody reads them."""
        if len(rows) > MAX_ROWS:
            raise ValueError(f"{len(rows)} rows do not fit the sort key's R{MAX_ROWS}")
        now = time.time()
        generation = str(int(now * 1000))
        rows_expire = int(now) + ROWS_TTL_SECONDS
        totals = {column: int(summary[column]) for column in SUMMARY_COLUMNS}
        with self.table.batch_writer() as batch:
            for rank, row in enumerate(rows, start=1):
                batch.put_item(Item={"PK": branch.key, "SK": row_key(feed_date, generation, rank),
                                     "rank": rank, "expires_at": rows_expire,
                                     **_encode_row(row)})
        self.table.put_item(Item={
            "PK": branch.key, "SK": f"SUM#{feed_date}", "feed_date": feed_date,
            "rows": len(rows), "expires_at": int(now) + SUMMARY_TTL_SECONDS, **totals})
        try:
            self.table.put_item(
                Item={"PK": branch.key, "SK": "META", "feed_date": feed_date,
                      "generation": generation, "rows": len(rows),
                      "rows_expire_at": rows_expire, **totals},
                ConditionExpression=Attr("PK").not_exists() | Attr("feed_date").lte(feed_date))
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    # -- The API's side -------------------------------------------------------

    def complete_day(self, branch: FollowedBranch) -> CompleteDay | None:
        """The day to show, or None if there is none (or its rows expired)."""
        item = self._meta(branch)
        # A META without rows_expire_at is from before generations: missing.
        if item is None or int(item.get("rows_expire_at", 0)) < time.time():
            return None
        return CompleteDay(item["feed_date"], item["generation"], _summary(item))

    def page(self, branch: FollowedBranch, feed_date: str, generation: str, after: int,
             limit: int, q: str | None = None) -> Page:
        """A write's rows after rank `after`, at most `limit`; with `q`, only
        those whose description contains it. Empty once they expired."""
        found: list[dict[str, Any]] = []
        query: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(branch.key)
                                 & Key("SK").between(row_key(feed_date, generation, after + 1),
                                                     row_key(feed_date, generation, MAX_ROWS))}
        if q:
            query["FilterExpression"] = Attr("desc_norm").contains(q.casefold())
        # One row past the page says whether there is a next one.
        while len(found) <= limit:
            if not q:
                query["Limit"] = limit + 1 - len(found)
            answer = self.table.query(**query)
            found.extend(answer["Items"])
            if "LastEvaluatedKey" not in answer:
                break
            query["ExclusiveStartKey"] = answer["LastEvaluatedKey"]
        rows = found[:limit]
        next_after = int(rows[-1]["rank"]) if len(found) > limit else None
        return Page([_decode_row(row) for row in rows], next_after)

    def claim_queueing(self, branch: FollowedBranch) -> bool:
        """Whether the caller is the one to queue this partition's precompute:
        False if someone did in the last few minutes."""
        now = int(time.time())
        try:
            self.table.put_item(
                Item={"PK": branch.key, "SK": "QUEUED", "expires_at": now + QUEUED_TTL_SECONDS},
                ConditionExpression=Attr("PK").not_exists() | Attr("expires_at").lt(now))
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True
