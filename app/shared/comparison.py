"""The comparison: a branch's own prices against its competitive set.

The one query behind the tablero and Mis Precios, shared by the API, which
runs it live (another radius, or a branch with no precompute yet), and the
precompute, which runs it once per followed branch and radius without a
LIMIT and stores every row in DynamoDB (`precomputed.py`). Every
function takes a psycopg connection with dict rows.

    import comparison
"""
from __future__ import annotations

from typing import Any

# Own price vs. competitor min/avg/max, restricted to the assortment both
# carry. MATERIALIZED on the two small CTEs stops the planner inlining and
# re-deriving them per join; `mine` and each competitor branch in `theirs`
# are index-only scans on idx_price_branch. See ARCHITECTURE.md's "conjunto
# competitivo" and app/db_migrator/schema.py for why this shape is fast.
#
# `product` is joined AFTER the GROUP BY, not before. A dense urban radius
# (measured: a CABA branch with 621 neighbours within 5 km) makes `theirs`
# fan out past a million rows before aggregation -- every neighbour branch's
# full assortment, not just the overlap with `mine`. Joining `product` at
# that width meant a million index probes into product_pkey for a result
# that only ever has one row per EAN; moving it after GROUP BY turns that
# into one probe per EAN in the *aggregated* result (thousands, not
# millions). Measured on that branch: ~7 s -> ~1.5 s combined with the
# work_mem bump in the database's parameter group (infra/modules/database).
# When a published minimum cannot be a real shelf price, the comparison
# against it is noise -- and because those rows carry the largest percentages,
# `ORDER BY pct_over_min DESC` handed them the whole first page. They are still
# returned (hiding data the merchant can see in the feed would be worse), but
# they sort last, behind every gap that can actually be acted on.
#
# The frontend mirrors these three thresholds in `lib/format.ts` to decide how
# to label such a row. Change them together.
IMPLAUSIBLE_PRICE_FLOOR = 10       # under this it is a placeholder, not a price
OUTLIER_RATIO = 8                  # this far under the set's own average is an outlier
MAX_CREDIBLE_GAP_PCT = 300         # past this it is a different product, not a gap

# The pieces the three competitive-set queries are built from, rather than
# three copies of the same SQL. They were copies once -- COMPARISON and
# COMPARISON_SUMMARY each carried their own -- and the copies drifted: the two
# `matched` bodies ended up differing only in three aggregate columns, which is
# how the tablero came to run this entire fan-out twice per load to answer one
# screen.
#
# `matched` is MATERIALIZED because DASHBOARD reads it twice, once for the rows
# and once for the counts. Postgres materialises a twice-referenced CTE anyway;
# saying so keeps that from quietly becoming two fan-outs again if a later
# reader drops one of the references.
COMPARISON_BASE = """
    WITH neighbor AS MATERIALIZED (
      SELECT other_company_id AS company_id, other_banner_id AS banner_id,
             other_branch_id AS branch_id
      FROM branch_neighbor
      WHERE company_id = %(company_id)s AND banner_id = %(banner_id)s
        AND branch_id = %(branch_id)s AND distance_m <= %(radius_m)s
    ),
    mine AS MATERIALIZED (
      SELECT p.ean, p.price FROM price p
      WHERE p.feed_date = %(feed_date)s AND p.company_id = %(company_id)s
        AND p.banner_id = %(banner_id)s AND p.branch_id = %(branch_id)s
        -- The description filter belongs HERE, not after the GROUP BY. The
        -- fan-out below is `mine` x every neighbour's assortment, so narrowing
        -- `mine` narrows everything downstream; filtering the aggregated
        -- result instead did the million-row join in full and then threw most
        -- of it away, which is why searching in Mis Precios cost the same as
        -- not searching. One index probe into product_pkey per row of this
        -- branch's own assortment -- thousands, not millions.
        AND (%(q)s::text IS NULL OR EXISTS (
              SELECT 1 FROM product pr
              WHERE pr.ean = p.ean AND pr.description ILIKE %(q)s))
    ),
    matched AS MATERIALIZED (
      SELECT m.ean, m.price AS own_price,
             min(t.price) AS min_price,
             round(avg(t.price), 2) AS avg_price,
             max(t.price) AS max_price,
             count(*) AS offers
      FROM mine m
      JOIN neighbor n ON TRUE
      JOIN price t ON t.feed_date = %(feed_date)s AND t.company_id = n.company_id
                   AND t.banner_id = n.banner_id AND t.branch_id = n.branch_id
                   AND t.ean = m.ean
      GROUP BY m.ean, m.price
    )
"""

# The product names, and the gap that orders the rows. Only the queries that
# return rows pay for this join.
SCORED = """
    scored AS (
      SELECT mt.ean, pr.description, pr.brand,
             mt.own_price, mt.min_price, mt.avg_price, mt.max_price, mt.offers,
             round(100 * (mt.own_price - mt.min_price) / nullif(mt.min_price, 0), 2)
               AS pct_over_min
      FROM matched mt
      LEFT JOIN product pr ON pr.ean = mt.ean
    )
"""

# Counts over `matched`, not `scored`: the position summary answers "where do I
# stand across the whole shared assortment", which a description filter must
# not move. `own_assortment` comes from `mine`, so it stays right for a branch
# that has products but no comparable competitor -- compared = 0 while the
# merchant still has a surtido to show.
TOTALS = """
    totals AS (
      SELECT count(*) AS compared,
             count(*) FILTER (WHERE own_price < min_price) AS cheaper,
             count(*) FILTER (WHERE own_price = min_price) AS matched_min,
             count(*) FILTER (WHERE own_price > min_price) AS dearer,
             (SELECT count(*) FROM mine) AS own_assortment
      FROM matched
    )
"""

# false sorts before true, so credible gaps come first and the rows whose
# minimum is not believable fall to the end. `ean` is the tiebreaker that makes
# LIMIT/OFFSET stable across pages.
WORST_FIRST = """
    ORDER BY (min_price < %(price_floor)s
              OR min_price < avg_price / %(outlier_ratio)s
              OR pct_over_min > %(max_gap_pct)s) ASC,
             pct_over_min DESC NULLS LAST,
             ean
"""

ROW_COLUMNS = ("ean", "description", "brand", "own_price", "min_price", "avg_price",
               "max_price", "offers", "pct_over_min")
SUMMARY_COLUMNS = ("compared", "cheaper", "matched_min", "dearer", "own_assortment")

COMPARISON = f"""
    {COMPARISON_BASE},
    {SCORED}
    SELECT {", ".join(ROW_COLUMNS)}
    FROM scored
    {WORST_FIRST}
    LIMIT %(limit)s OFFSET %(offset)s
"""


def page(connection: Any, company_id: int, banner_id: int, branch_id: str,
         feed_date: str, radius_km: float, q: str | None, limit: int,
         offset: int) -> list[dict[str, Any]]:
    """A page of the rows, worst gap first; `q` filters them by description."""
    return connection.execute(COMPARISON, _comparison_params(
        company_id, banner_id, branch_id, feed_date, radius_km,
        q=f"%{q}%" if q else None, limit=limit, offset=offset)).fetchall()


COMPARISON_SUMMARY = f"""
    {COMPARISON_BASE},
    {TOTALS}
    SELECT {", ".join(SUMMARY_COLUMNS)} FROM totals
"""


def _comparison_params(company_id: int, banner_id: int, branch_id: str, feed_date: str,
                       radius_km: float, **extra: Any) -> dict[str, Any]:
    return {
        "company_id": company_id, "banner_id": banner_id, "branch_id": branch_id,
        "feed_date": feed_date, "radius_m": radius_km * 1000,
        "price_floor": IMPLAUSIBLE_PRICE_FLOOR, "outlier_ratio": OUTLIER_RATIO,
        "max_gap_pct": MAX_CREDIBLE_GAP_PCT, **extra,
    }


def summary(connection: Any, company_id: int, banner_id: int, branch_id: str,
            feed_date: str, radius_km: float) -> dict[str, Any]:
    return connection.execute(COMPARISON_SUMMARY, _comparison_params(
        company_id, banner_id, branch_id, feed_date, radius_km, q=None)).fetchone()


# What the tablero asks for, in one pass over `matched` instead of two.
#
# The LATERAL hangs the page off `totals`, not the other way round: `totals` is
# an ungrouped aggregate, so it always returns exactly one row, and a LEFT JOIN
# to it still carries the counts when there is nothing to rank. That case is
# real -- a branch whose radius holds no competitor selling anything it sells
# has compared = 0 and own_assortment > 0, and the position strip shows both.
# A CROSS JOIN, or window aggregates over the rows, would return no rows at all
# there and the screen would have nothing to say.
DASHBOARD = f"""
    {COMPARISON_BASE},
    {SCORED},
    {TOTALS}
    SELECT {", ".join("t." + c for c in SUMMARY_COLUMNS)},
           {", ".join("s." + c for c in ROW_COLUMNS)}
    FROM totals t
    LEFT JOIN LATERAL (
      SELECT {", ".join(ROW_COLUMNS)}
      FROM scored
      {WORST_FIRST}
      LIMIT %(limit)s
    ) s ON TRUE
"""


def empty_summary() -> dict[str, int]:
    """The position summary for a branch there is nothing to say about yet."""
    return dict.fromkeys(SUMMARY_COLUMNS, 0)


def dashboard(connection: Any, company_id: int, banner_id: int, branch_id: str,
              feed_date: str, radius_km: float, limit: int | None) -> dict[str, Any]:
    """The position summary and the worst gaps, from one scan: the first
    `limit` of them, or with None all of them (what the precompute
    stores).

    Splitting the flat result back apart is this function's job rather than the
    caller's: the totals repeat on every row because SQL has no other way to
    return two shapes at once, and that is an artefact of the transport, not
    something the API should hand to a browser.
    """
    rows = connection.execute(DASHBOARD, _comparison_params(
        company_id, banner_id, branch_id, feed_date, radius_km,
        q=None, limit=limit)).fetchall()
    if not rows:
        # `totals` always produces a row, so this only happens if the branch
        # has no prices for the date at all -- `mine` empty, nothing to count.
        return {"summary": empty_summary(), "worst": []}
    totals = {column: rows[0][column] for column in SUMMARY_COLUMNS}
    # `ean` is part of price's key and never null, so a null one is the
    # LATERAL's no-match row rather than a product: counts, but nothing ranked.
    worst = [{column: row[column] for column in ROW_COLUMNS}
             for row in rows if row["ean"] is not None]
    return {"summary": totals, "worst": worst}
