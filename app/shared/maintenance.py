"""Catalogue-wide maintenance: the two rebuilds nothing does per job.

Both operate on data shared by every org, both are a full rebuild rather than
an incremental update, and both depend only on tables that change at most once
a day -- which is why they are not inside a worker job and not inside a
request. They live in app/shared, next to `common.py`, because both
the day close (once a day, when its last chain is in) and the backend (on
POST /admin/*) call them, and neither owns the other.

Nothing here imports from FastAPI or from `app/api/db.py`: `connection` is any
psycopg connection.

    import maintenance
"""
from __future__ import annotations

from typing import Any

# Cross-company branch pairs within the 10 km cap org_branch.radius_km is
# bound to. Depends only on `branch`, which changes at most once a day, so it
# is refreshed on demand rather than recomputed inside every worker job.
REFRESH_NEIGHBORS = """
    TRUNCATE branch_neighbor;
    INSERT INTO branch_neighbor
    SELECT a.company_id, a.banner_id, a.branch_id,
           b.company_id, b.banner_id, b.branch_id,
           earth_distance(ll_to_earth(a.latitude, a.longitude),
                          ll_to_earth(b.latitude, b.longitude))::int
    FROM branch a
    JOIN branch b
      ON b.company_id <> a.company_id
     AND earth_box(ll_to_earth(a.latitude, a.longitude), 10000)
           @> ll_to_earth(b.latitude, b.longitude)
     AND earth_distance(ll_to_earth(a.latitude, a.longitude),
                        ll_to_earth(b.latitude, b.longitude)) <= 10000
    WHERE a.latitude IS NOT NULL AND a.longitude IS NOT NULL
      AND b.latitude IS NOT NULL AND b.longitude IS NOT NULL
"""


def refresh_neighbors(connection: Any) -> int:
    connection.execute(REFRESH_NEIGHBORS)
    return connection.execute("SELECT count(*) AS n FROM branch_neighbor").fetchone()["n"]


# The same pairs, for one branch only, both ways: what a merchant's own branch
# needs the moment it is created or moved, instead of waiting for the next day
# close to rebuild everything. A few hundred rows, not 600,000.
REFRESH_BRANCH_NEIGHBORS = """
    WITH me AS (
      SELECT company_id, banner_id, branch_id, ll_to_earth(latitude, longitude) AS at
      FROM branch
      WHERE company_id = %(company_id)s AND banner_id = %(banner_id)s
        AND branch_id = %(branch_id)s AND latitude IS NOT NULL AND longitude IS NOT NULL
    ),
    near AS (
      SELECT me.company_id, me.banner_id, me.branch_id,
             b.company_id AS other_company_id, b.banner_id AS other_banner_id,
             b.branch_id AS other_branch_id,
             earth_distance(me.at, ll_to_earth(b.latitude, b.longitude))::int AS distance_m
      FROM me
      JOIN branch b
        ON b.company_id <> me.company_id
       AND b.latitude IS NOT NULL AND b.longitude IS NOT NULL
       AND earth_box(me.at, 10000) @> ll_to_earth(b.latitude, b.longitude)
       AND earth_distance(me.at, ll_to_earth(b.latitude, b.longitude)) <= 10000
    )
    INSERT INTO branch_neighbor
    SELECT * FROM near
    UNION ALL
    SELECT other_company_id, other_banner_id, other_branch_id,
           company_id, banner_id, branch_id, distance_m FROM near
"""


def refresh_branch_neighbors(connection: Any, company_id: int, banner_id: int,
                             branch_id: str) -> int:
    """Replace one branch's pairs, both ways. Run it inside the transaction
    that moved the branch."""
    key = {"company_id": company_id, "banner_id": banner_id, "branch_id": branch_id}
    connection.execute("""
        DELETE FROM branch_neighbor
        WHERE (company_id, banner_id, branch_id)
              = (%(company_id)s, %(banner_id)s, %(branch_id)s)
           OR (other_company_id, other_banner_id, other_branch_id)
              = (%(company_id)s, %(banner_id)s, %(branch_id)s)
    """, key)
    return connection.execute(REFRESH_BRANCH_NEIGHBORS, key).rowcount


# Majority vote across every chain's own wording for a barcode, accent-folded
# and lower-cased for search, with a best-effort size parse.
NORMALIZE_CATALOG = r"""
    INSERT INTO product_norm (ean, name_norm, brand_norm, size_value, size_unit, updated_at)
    SELECT v.ean, v.name_norm, v.brand_norm,
           nullif(replace(m[1], ',', '.'), '')::numeric, m[2], now()
    FROM (
      SELECT DISTINCT ON (ean)
             ean,
             btrim(regexp_replace(f_unaccent(lower(description)), '\s+', ' ', 'g')) AS name_norm,
             btrim(f_unaccent(lower(brand))) AS brand_norm,
             count(*) OVER (PARTITION BY ean, lower(description)) AS votes
      FROM product_name
      WHERE description IS NOT NULL AND description <> ''
      ORDER BY ean, count(*) OVER (PARTITION BY ean, lower(description)) DESC, description
    ) v
    -- \y and not \b: in PostgreSQL's regex flavour \b is the backspace
    -- character, so a pattern ending in \b never matches anything. \y is the
    -- word boundary every other flavour spells \b.
    LEFT JOIN LATERAL regexp_match(
      v.name_norm, '([0-9]+(?:[.,][0-9]+)?)\s*(kg|gr|g|ml|cc|lt|l|un|u)\y') AS m ON TRUE
    ON CONFLICT (ean) DO UPDATE SET
        name_norm = EXCLUDED.name_norm, brand_norm = EXCLUDED.brand_norm,
        size_value = EXCLUDED.size_value, size_unit = EXCLUDED.size_unit,
        updated_at = now()
"""


def normalize_catalog(connection: Any) -> int:
    connection.execute(NORMALIZE_CATALOG)
    return connection.execute("SELECT count(*) AS n FROM product_norm").fetchone()["n"]
