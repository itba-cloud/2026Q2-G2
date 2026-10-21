"""The schema migrator: a Lambda that Terraform invokes during the apply, once
the database exists. Applies the whole schema, idempotently, so invoking it
again against a database that already has it changes nothing.

    handler(event, context) -> {"tables": [...]}

It answers with the tables the database has afterwards, which is what Deploy
and `make verify` look at. From the repo, against a database at hand:

    PYTHONPATH=app/shared python app/db_migrator/main.py
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg

# `schema` is this same folder: next to this file in the Lambda package.
import schema
# `common` lives in app/shared: next to this file in the Lambda package, on
# PYTHONPATH when run from the repo.
from common import configure_logging, connect

configure_logging()
log = logging.getLogger("migrator")

# How many months of `price` partitions are kept behind the current one.
RETENTION_MONTHS = 6

# Ordinary and partitioned tables, but not the partitions themselves: the
# months of `price` move with the calendar, the schema does not.
TABLES = """
SELECT c.relname AS name FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
ORDER BY c.relname
"""


def schedule_partition_maintenance(connection: Any) -> None:
    """Hand the nightly partition roll to pg_cron, inside the database.

    Best effort on purpose: pg_cron needs `shared_preload_libraries=pg_cron`,
    which the instance's parameter group sets from its first boot, but a
    database without it (a local Postgres) is no reason to fail the
    migration. The schema is applied and the current partitions exist either
    way; the only thing missing is the automatic roll months out.

    `cron.schedule` with a jobname that already exists updates it rather than
    adding a second one, which keeps this as replayable as the rest. 03:00 UTC
    is before the ingestion window, so it never competes with a load.
    """
    try:
        connection.execute("CREATE EXTENSION IF NOT EXISTS pg_cron;")
        connection.execute(
            "SELECT cron.schedule('price-partitions', '0 3 * * *', "
            f"'SELECT price_maintain_partitions({RETENTION_MONTHS});');")
        log.info("pg_cron: price-partitions scheduled for 03:00 UTC")
    except psycopg.Error as error:
        log.warning("pg_cron unavailable, partitions will not roll on their own "
                    "(%s)", str(error).strip().splitlines()[0])


def migrate(connection: Any) -> list[str]:
    """Apply the schema and return the tables the database has afterwards."""
    for name, statements in (("extensions", schema.EXTENSIONS), ("core", schema.CORE),
                              ("portal", schema.PORTAL), ("pipeline", schema.PIPELINE),
                              ("indexes", schema.INDEXES),
                              ("cleanup", schema.DROP_REDUNDANT)):
        connection.execute(statements)
        log.info("schema: %s applied", name)

    # `price` is partitioned and has no DEFAULT partition, so the months it
    # will be written to have to exist before the first INSERT -- including the
    # months behind us, since the staged SEPA archive, a merchant's upload and
    # any reprocess all carry a feed_date that is not today.
    connection.execute("SELECT price_maintain_partitions(%s);", (RETENTION_MONTHS,))
    log.info("schema: price partitions ensured")
    schedule_partition_maintenance(connection)

    return [row["name"] for row in connection.execute(TABLES).fetchall()]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    with connect() as connection:
        tables = migrate(connection)
    log.info("schema: %d tables", len(tables))
    return {"tables": tables}


if __name__ == "__main__":
    print(handler({}, None))
