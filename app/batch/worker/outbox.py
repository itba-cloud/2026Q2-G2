"""The chain-processed notice, through the outbox.

Every job that settles -- its prices loaded, or failed for good -- leaves a
notice in `outbox`, written in the same transaction that settles it: the
notice exists exactly when the job's outcome does. After the commit,
publish_pending() sends the notices not sent yet to the cadena-procesada
topic and marks each one sent. A publish that fails leaves its notice for
the next call, so a notice is never lost and never announced before what it
announces was stored; it may, rarely, go out twice (published, then the mark
lost), which its consumers tolerate.

The message is the notice as JSON, and its fields again as message
attributes: SNS filters each subscription on those.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any

from psycopg.types.json import Jsonb

RECORD = "INSERT INTO outbox (payload) VALUES (%s)"

# One at a time, each in its own transaction: a notice is marked as soon as
# it is published, and SKIP LOCKED keeps two workers off the same one.
NEXT_UNSENT = """
    SELECT event_id, payload FROM outbox
    WHERE sent_at IS NULL
    ORDER BY created_at, event_id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
"""

MARK_SENT = "UPDATE outbox SET sent_at = now() WHERE event_id = %s"

# The attributes SNS filters on, and their types: a Number one can be
# filtered with a numeric range (price_changes > 0).
ATTRIBUTE_TYPES = {
    "feed_date": "String",
    "company_id": "Number",
    "kind": "String",
    "status": "String",
    "price_changes": "Number",
}


@dataclasses.dataclass(frozen=True)
class ChainProcessed:
    """A chain's day, processed: loaded (`done`) or failed for good."""
    feed_date: str
    company_id: int
    kind: str  # sepa or upload
    status: str  # done or failed
    price_changes: int = 0


def record(connection: Any, notice: ChainProcessed) -> None:
    """Store the notice. Call it inside the transaction that settles its job."""
    connection.execute(RECORD, (Jsonb(dataclasses.asdict(notice)),))


def subject(payload: dict[str, Any]) -> str:
    """What the admin's email says, the one subscription that reads it. SNS
    takes only ASCII here."""
    if payload["status"] == "failed":
        return (f"LGDC: error al procesar la cadena {payload['company_id']} "
                f"del {payload['feed_date']}")
    return f"LGDC: cadena {payload['company_id']} del {payload['feed_date']} procesada"


def publish(sns: Any, topic_arn: str, payload: dict[str, Any]) -> None:
    sns.publish(
        TopicArn=topic_arn,
        Message=json.dumps(payload),
        Subject=subject(payload),
        MessageAttributes={
            name: {"DataType": kind, "StringValue": str(payload[name])}
            for name, kind in ATTRIBUTE_TYPES.items()
        },
    )


def publish_pending(connection: Any, sns: Any, topic_arn: str) -> int:
    """Publish every notice not sent yet, oldest first. How many it sent.
    Raises on the first publish that fails, leaving that one and the rest
    for the next call."""
    sent = 0
    while True:
        with connection.transaction():
            row = connection.execute(NEXT_UNSENT).fetchone()
            if row is None:
                return sent
            publish(sns, topic_arn, row["payload"])
            connection.execute(MARK_SENT, (row["event_id"],))
        sent += 1
