"""The competitive set and the isolation between orgs, on branches the checks
load themselves, so they run the same on an account with no SEPA data yet.

Three synthetic chains, one branch each, loaded through the pipeline like any
chain: the home branch, a competitor 500 m away and one 5 km away, all three
selling the same products, each cheaper than the last. They sit in the South
Atlantic, more than 1,000 km from any real branch, so they only neighbor each
other and never enter a real comparison.

What it establishes: every screen's reads answer on a followed branch; an org
never sees another's branches, their data or its rules; and a smaller radius
shrinks the competitive set, both the competitors and the offers each product
is compared against. Plus the rest of what backend/verify.py checked on the
competitive set: the competitors page through it in order, the comparison
comes back shaped right, the tablero says what the two endpoints it replaced
said, and replacing a rule's products twice duplicates nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from archives import JOB_TIMEOUT, job, processed_after, put_raw, sepa_archive
from cognito import owner_of_new_org
from harness import SCREEN_READS, Checks, Stack, api, statuses, wait_for

HOME, NEAR, FAR = 899000011, 899000012, 899000013
LATITUDE, LONGITUDE = -45.0, -40.0
# Each chain's offset north of the home branch, in degrees of latitude, and
# its price for every product. On earthdistance's sphere 0.0045° is ~500 m.
CHAINS = {HOME: (0.0, 100.0), NEAR: (0.0045, 90.0), FAR: (0.045, 80.0)}
NEAR_M, FAR_M = 500, 5000
EANS = [f"77999991{index:05d}" for index in range(5)]

# The day close rebuilds the neighbors once every chain of the day is in.
NEIGHBORS_TIMEOUT = 5 * 60


@dataclass
class Home:
    """The home branch, followed by its org's owner."""
    token: str
    path: str


def competitors(stack: Stack, home: Home, token: str | None = None,
                **params: Any) -> dict[str, Any]:
    query = "&".join(f"{name}={value}" for name, value in {"limit": 500, **params}.items())
    return api(stack, "GET", f"{home.path}/competitors?{query}",
               token or home.token).json() or {}


def comparison(stack: Stack, home: Home, radius_km: float, **params: Any) -> list[dict[str, Any]]:
    """The first page of Mis Precios: its rows."""
    query = "".join(f"&{name}={value}" for name, value in params.items())
    page = api(stack, "GET", f"{home.path}/comparison?radius_km={radius_km}&limit=50{query}",
               home.token).json() or {}
    return page.get("items", []) if isinstance(page, dict) else []


def load_home(checks: Checks, stack: Stack) -> Home | None:
    checks.section("Competitive set · three branches loaded through the pipeline")
    token = owner_of_new_org(stack, "LGDC verify, conjunto")
    previous = {chain: job(stack, token, chain) for chain in CHAINS}
    for chain, (north, price) in CHAINS.items():
        put_raw(stack, chain, "verify.zip", sepa_archive(
            chain, dict.fromkeys(EANS, price), latitude=LATITUDE + north, longitude=LONGITUDE))

    done = {chain: wait_for(stack, processed_after(stack, token, chain, previous[chain], "done"),
                            JOB_TIMEOUT) for chain in CHAINS}
    checks.expect("the three chains' jobs end in done", all(done.values()),
                  str({chain: job(stack, token, chain) for chain, row in done.items() if not row}))
    if not all(done.values()):
        return None

    home = Home(token, f"/branches/{HOME}/1/1")
    followed = api(stack, "POST", "/me/branches", token,
                   {"company_id": HOME, "banner_id": 1, "branch_id": "1", "radius_km": 10})
    checks.expect("the org follows the home branch", followed.status == 201,
                  f"{followed.status} {followed.body[:200]}")
    rebuilt = wait_for(stack, lambda: competitors(stack, home, radius_km=10).get("total") == 2,
                       NEIGHBORS_TIMEOUT)
    checks.expect("the day close rebuilt the neighbors: the other two are within 10 km",
                  bool(rebuilt), str(competitors(stack, home, radius_km=10)))
    return home


def check_screens(checks: Checks, stack: Stack, home: Home) -> None:
    checks.section("Screens · every page's reads answer")
    reads = statuses(stack, home.token, SCREEN_READS)
    checks.expect(f"the {len(SCREEN_READS)} reads answer 200",
                  all(status == 200 for status in reads.values()), str(reads))

    path = home.path
    branch_reads = statuses(stack, home.token, (
        path,
        f"{path}/dashboard",
        f"{path}/prices",
        f"{path}/comparison",
        f"{path}/comparison/summary",
        f"{path}/competitors",
        f"{path}/changes",
        f"{path}/head-to-head?vs={NEAR}:1:1",
        f"{path}/products/{EANS[0]}/history",
        f"{path}/products/{EANS[0]}/competitors",
    ))
    checks.expect(f"the branch's {len(branch_reads)} reads answer 200",
                  all(status == 200 for status in branch_reads.values()), str(branch_reads))


def check_isolation(checks: Checks, stack: Stack, home: Home) -> None:
    checks.section("Organizations · an org never sees another's")
    rule = api(stack, "POST", "/rules", home.token,
               {"name": "LGDC verify", "company_id": HOME, "banner_id": 1, "branch_id": "1"})
    rule_id = (rule.json() or {}).get("rule_id")
    checks.expect("the first org sets a rule on its branch", rule.status == 201 and bool(rule_id),
                  f"{rule.status} {rule.body[:200]}")
    if not rule_id:
        return

    # The critical-product list is replaced, not appended to.
    for _ in range(2):
        api(stack, "PUT", f"/rules/{rule_id}/products", home.token, {"products": EANS[:2]})
    listed = next((row.get("products") for row in api(stack, "GET", "/rules", home.token).json()
                   or [] if row.get("rule_id") == rule_id), None)
    checks.expect("replacing its products twice leaves them once",
                  sorted(listed or []) == EANS[:2], str(listed))

    other = owner_of_new_org(stack, "LGDC verify, otra")
    theirs = api(stack, "GET", "/me/branches", other).json()
    checks.expect("the other org follows nothing", theirs == [], str(theirs))
    refused = statuses(stack, other, (f"{home.path}/dashboard", f"{home.path}/comparison",
                                      f"{home.path}/competitors", f"{home.path}/prices"))
    checks.expect("the branch's dashboard, comparison, competitors and prices are 404 for it",
                  all(status == 404 for status in refused.values()), str(refused))
    rules = api(stack, "GET", "/rules", other).json()
    checks.expect("it sees none of the first org's rules", rules == [], str(rules))
    deleted = api(stack, "DELETE", f"/rules/{rule_id}", other)
    checks.expect("and cannot delete one (404)", deleted.status == 404, f"{deleted.status}")

    mine = api(stack, "GET", "/me/branches", home.token).json() or []
    checks.expect("the first org still follows its branch", len(mine) == 1, str(mine))
    kept = [row.get("rule_id") for row in api(stack, "GET", "/rules", home.token).json() or []]
    checks.expect("and still has its rule", kept == [rule_id], str(kept))
    removed = api(stack, "DELETE", f"/rules/{rule_id}", home.token)
    checks.expect("which it can delete (204)", removed.status == 204, f"{removed.status}")


def check_radius(checks: Checks, stack: Stack, home: Home) -> None:
    checks.section("Radius · a smaller radius shrinks the competitive set")
    small, large = competitors(stack, home, radius_km=1), competitors(stack, home, radius_km=10)
    checks.expect(f"10 km holds both competitors, 1 km only the one 500 m away "
                  f"({large.get('total')} vs {small.get('total')})",
                  large.get("total") == 2 and small.get("total") == 1, f"{small} {large}")
    nearest = [row["company_id"] for row in large.get("items", [])]
    checks.expect("nearest first", nearest == [NEAR, FAR], str(nearest))
    distances = [row["distance_m"] for row in large.get("items", [])]
    checks.expect("each at the distance it is",
                  len(distances) == 2 and abs(distances[0] - NEAR_M) < NEAR_M * 0.05
                  and abs(distances[1] - FAR_M) < FAR_M * 0.05, str(distances))

    narrow, wide = comparison(stack, home, 1), comparison(stack, home, 10)
    checks.expect(f"the comparison covers the {len(EANS)} products at both radii",
                  len(narrow) == len(wide) == len(EANS), f"{len(narrow)}, {len(wide)}")
    checks.expect("at 1 km each product is compared against one offer, the 90.00",
                  all(row["offers"] == 1 and float(row["min_price"]) == 90 for row in narrow),
                  str(narrow[:1]))
    checks.expect("at 10 km against two, the cheapest the 80.00 from 5 km away",
                  all(row["offers"] == 2 and float(row["min_price"]) == 80 for row in wide),
                  str(wide[:1]))

    # The radius the org saves for its branch, the one the screens use when
    # they pass none.
    patched = api(stack, "PATCH", f"/me{home.path}", home.token, {"radius_km": 1})
    saved = competitors(stack, home)
    checks.expect("saving 1 km as the branch's radius leaves one competitor",
                  patched.status == 200 and saved.get("total") == 1,
                  f"{patched.status} {saved}")


def check_paging(checks: Checks, stack: Stack, home: Home) -> None:
    checks.section("Competitors · the page and the total are separate answers")
    whole = competitors(stack, home, radius_km=10)
    first = competitors(stack, home, radius_km=10, limit=1)
    checks.expect("a one-row page still reports the whole total",
                  first.get("total") == whole.get("total") == 2 and len(first.get("items", [])) == 1,
                  str(first))
    walked = [competitors(stack, home, radius_km=10, limit=1, offset=offset).get("items", [])
              for offset in range(2)]
    keys = [page[0]["company_id"] if page else None for page in walked]
    checks.expect("paging one row at a time walks the set in order, no repeats",
                  keys == [NEAR, FAR], str(keys))
    named = competitors(stack, home, radius_km=10, q=FAR)
    checks.expect("q narrows the set to the branches it names",
                  named.get("total") == 1
                  and [row["company_id"] for row in named.get("items", [])] == [FAR], str(named))


def check_comparison(checks: Checks, stack: Stack, home: Home) -> None:
    checks.section("Comparison · the shared assortment, shaped right")
    rows = comparison(stack, home, 10)
    checks.expect("each row carries its own price, the competitors' range and the offers",
                  bool(rows) and all({"own_price", "min_price", "max_price", "offers"} <= set(row)
                                     for row in rows), str(rows[:1]))
    # NUMERIC comes back as JSON strings: compared as text, "9.00" > "144.00".
    checks.expect("its own 100.00 against a range from 80.00 to 90.00",
                  all((float(row["own_price"]), float(row["min_price"]), float(row["max_price"]))
                      == (100, 80, 90) for row in rows), str(rows[:1]))

    dashboard = api(stack, "GET", f"{home.path}/dashboard?radius_km=10&limit=20",
                    home.token).json() or {}
    feed_date = dashboard.get("feed_date")
    checks.expect("the tablero resolves the feed date itself", bool(feed_date), str(dashboard))
    if not feed_date:
        return
    # Pinned to the date the tablero used, as the screen does.
    summary = api(stack, "GET", f"{home.path}/comparison/summary?radius_km=10"
                                f"&feed_date={feed_date}", home.token).json()
    worst = comparison(stack, home, 10, feed_date=feed_date, limit=20)
    checks.expect("its summary is /comparison/summary's", dashboard.get("summary") == summary,
                  f"{dashboard.get('summary')} vs {summary}")
    checks.expect("and its rows, /comparison's first page", dashboard.get("worst") == worst)
    checks.expect("all five products compared, all dearer than both competitors",
                  (summary or {}).get("compared") == len(EANS)
                  and (summary or {}).get("dearer") == len(EANS), str(summary))

    # A radius that catches nobody (not 0: the API reads 0 as "none given").
    alone = api(stack, "GET", f"{home.path}/dashboard?radius_km=0.001&feed_date={feed_date}",
                home.token).json() or {}
    checks.expect("a radius that catches nobody compares nothing, and still counts "
                  "the branch's own products",
                  (alone.get("summary") or {}).get("compared") == 0
                  and (alone.get("summary") or {}).get("own_assortment") == len(EANS)
                  and alone.get("worst") == [], str(alone))


def run(checks: Checks, stack: Stack) -> None:
    home = load_home(checks, stack)
    if home is None:
        return
    check_screens(checks, stack, home)
    check_isolation(checks, stack, home)
    check_paging(checks, stack, home)
    check_comparison(checks, stack, home)
    # Last: it saves 1 km as the branch's radius.
    check_radius(checks, stack, home)
