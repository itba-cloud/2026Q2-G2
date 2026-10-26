"""Alert-matcher Lambda.

Consumes the alerts queue, which the cadena-procesada topic fills with the
notices of the chains that loaded with price changes. For each notice it
matches that chain's day of changes (`price_change`, which the worker wrote
with the prices) against every org's watch rules, and stores the matches in
`alert`, where the portal reads them.

Idempotent: an alert is unique per rule, day, product and branch, so a
notice delivered twice raises nothing twice. A notice that fails comes back
alone (the batch reports which), and after the queue's max_receive_count
goes to its DLQ.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from common import configure_logging, connect

configure_logging()
log = logging.getLogger("matcher")

# A price change reaches an org's feed only if: it happened within the radius
# the rule (or its branch's default) covers, it clears the rule's threshold,
# it names this competitor or the rule leaves the competitor open, and either
# the rule watches every product or this EAN is on its critical list.
#
# The radius comes from `branch_neighbor`, which the catalogue maintenance
# rebuilds: a branch loaded for the first time has no neighbors until then,
# and its changes raise nothing, as if there were none.
RAISE_ALERTS = """
    INSERT INTO alert (org_id, rule_id, feed_date, ean, company_id, banner_id, branch_id,
                       previous_price, price, delta_pct)
    SELECT r.org_id, r.rule_id, c.feed_date, c.ean, c.company_id, c.banner_id, c.branch_id,
           c.previous_price, c.price, c.delta_pct
    FROM price_change c
    JOIN branch_neighbor n
      ON  n.other_company_id = c.company_id
      AND n.other_banner_id  = c.banner_id
      AND n.other_branch_id  = c.branch_id
    JOIN watch_rule r
      ON  r.company_id = n.company_id
      AND r.banner_id  = n.banner_id
      AND r.branch_id  = n.branch_id
      AND r.enabled
    JOIN org_branch ob
      ON  ob.org_id     = r.org_id
      AND ob.company_id = r.company_id
      AND ob.banner_id  = r.banner_id
      AND ob.branch_id  = r.branch_id
    WHERE c.feed_date  = %s
      AND c.company_id = %s
      AND n.distance_m <= coalesce(r.radius_km, ob.radius_km) * 1000
      AND abs(c.delta_pct) >= r.threshold_pct
      AND (r.target_company_id IS NULL
           OR (r.target_company_id = c.company_id
               AND (r.target_banner_id IS NULL OR r.target_banner_id = c.banner_id)
               AND (r.target_branch_id IS NULL OR r.target_branch_id = c.branch_id)))
      AND (NOT EXISTS (SELECT 1 FROM watch_product w WHERE w.rule_id = r.rule_id)
           OR EXISTS (SELECT 1 FROM watch_product w
                      WHERE w.rule_id = r.rule_id AND w.ean = c.ean))
    ON CONFLICT (rule_id, feed_date, ean, company_id, banner_id, branch_id) DO NOTHING
"""

# One connection per execution environment, kept between invocations: the
# Proxy pools it, and opening one costs a Secrets Manager read.
_connection: Any = None


def database() -> Any:
    global _connection
    if _connection is None or _connection.closed or _connection.broken:
        _connection = connect()
    return _connection


def raise_alerts(connection: Any, feed_date: str, company_id: int) -> int:
    """Match a chain's changes of a day against every org's rules. How many
    alerts it stored."""
    return connection.execute(RAISE_ALERTS, (feed_date, company_id)).rowcount


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """A batch from the alerts queue. Returns the messages that failed, so
    SQS gives back only those."""
    failures = []
    for record in event.get("Records", []):
        try:
            notice = json.loads(record["body"])
            raised = raise_alerts(database(), notice["feed_date"], int(notice["company_id"]))
            log.info("chain %s, %s: %d price change(s), %d alert(s) raised",
                     notice["company_id"], notice["feed_date"],
                     notice.get("price_changes", 0), raised)
        except Exception:
            log.exception("notice %s failed: %s", record.get("messageId"),
                          record.get("body", "")[:200])
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
