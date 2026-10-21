"""The database (ticket 03): after Deploy the schema is there, applying it
again changes nothing, and the API reaches Postgres through the RDS Proxy."""
from __future__ import annotations

import json
from typing import Any

from harness import Checks, Stack, api

# The tables the TP3 pipeline adds, plus one from each of the older groups.
EXPECTED_TABLES = {"processed_message", "outbox", "feed_day", "ingest_job", "price", "org"}


def migrate(stack: Stack) -> tuple[bool, Any]:
    """Invoke the migrator the way Terraform does; (succeeded, its answer)."""
    response = stack.lambda_.invoke(FunctionName=stack.outputs["db_migrator_function_name"],
                                    Payload=b"{}")
    body = json.loads(response["Payload"].read())
    return "FunctionError" not in response, body


def check_schema(checks: Checks, stack: Stack) -> None:
    checks.section("Schema · applied during the apply, idempotent")
    applied = set(stack.outputs.get("db_schema_tables") or [])
    missing = EXPECTED_TABLES - applied
    checks.expect("the apply's migration created the TP3 tables", not missing,
                  f"missing: {sorted(missing)}")

    first_ok, first = migrate(stack)
    checks.expect("migrating again succeeds", first_ok, str(first))
    second_ok, second = migrate(stack)
    checks.expect("and once more", second_ok, str(second))
    if first_ok and second_ok:
        checks.expect("both leave the same tables the apply left",
                      set(first["tables"]) == set(second["tables"]) == applied,
                      f"apply {sorted(applied)}, then {first['tables']}, then {second['tables']}")


def check_ready(checks: Checks, stack: Stack) -> None:
    checks.section("Ready · the API reaches the database through the Proxy")
    ready = api(stack, "GET", "/ready")
    checks.expect("GET /api/ready is 200", ready.status == 200, f"{ready.status} {ready.body}")


def run(checks: Checks, stack: Stack) -> None:
    check_schema(checks, stack)
    check_ready(checks, stack)
