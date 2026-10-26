"""The precompute and the tablero's reads (ticket 12).

The precompute Lambda consumes the precompute queue, where the day close (and
the API) leave one entry per followed branch and radius: it runs the
comparison once and writes it to the DynamoDB table lgdc-comparison, the rows
first, then the summary, and last the META item that says which day is
complete. The tablero and Mis Precios read from there whenever the branch's
configured radius has a META; otherwise they answer with the live query, and
queue the precompute.

What it establishes, through the stack's doors: the function consumes the
queue, at most 2 at a time; the table is on-demand with a TTL; a branch with
no precompute answers live and gets queued, and then answers from the
precompute, the tablero and Mis Precios alike (the latter at a radius the
branch switches to, since the precompute lands within seconds of the first
read); a merchant's upload and a chain within its radius end up in the
precomputed tablero, its worst 20 first; Mis Precios pages through it with an
opaque cursor, with or without a description filter, from the precompute and
live alike; another radius answers live with the same numbers; and META moves
to a newer day, never to an older one.

Whether an answer came from the precompute or the live query is its `source`.
What the function did with an entry is read from its log, one line per entry,
like the day close's: the queue is the function's, and its entries cannot be
read without taking them.

The merchant is a new one every run (a new company), at (-60, -30) in the
South Atlantic, more than 500 km from every other suite's branches; its rival
is chain 899000051, 500 m north. Both load a day of the run's own, between
100 and 160 days back, so the rival's notice closes that day and rebuilds the
neighbors with the new merchant in them.
"""
from __future__ import annotations

import json
import time
from typing import Any

from archives import JOB_TIMEOUT, job, processed_after, put_raw, sepa_archive
from close import free_days
from cognito import owner_of_new_org
from harness import Checks, Stack, api, wait_for
from uploads import portal_origin, post_file, settled

RIVAL = 899000051
LATITUDE, LONGITUDE = -60.0, -30.0
# On earthdistance's sphere 0.0045° of latitude is ~500 m.
RIVAL_NORTH = 0.0045
# The merchant's branch is followed at the default radius from the start.
CONFIGURED_RADIUS = 5
# 25 products, more than a tablero's 20. The merchant sells them all at
# 100.00; the rival sells the first at 100.00 and each next one a peso
# cheaper, so each gap is bigger than the last.
EANS = [f"77999995{index:05d}" for index in range(25)]
OWN_PRICE = 100.0
RIVAL_PRICES = {ean: OWN_PRICE - index for index, ean in enumerate(EANS)}
# Worst gap first: the last product, the rival's cheapest.
WORST_FIRST = list(reversed(EANS))
# The ten barcodes from 7799999500010 to ...19, which a description filter
# finds by their digits: every description carries its barcode.
FILTER = "779999950001"
FILTERED = [ean for ean in WORST_FIRST if FILTER in ean]

# The function's result lines start with this.
MARKER = "precompute "
# An entry waits behind every other followed branch the close queued, two
# at a time.
PRECOMPUTE_TIMEOUT = 10 * 60


def price_list() -> bytes:
    rows = [f"{ean},{OWN_PRICE:.2f},PRODUCTO PRECALCULO {ean}" for ean in EANS]
    return ("ean,price,description\n" + "\n".join(rows) + "\n").encode()


def dashboard(stack: Stack, token: str, path: str, **params: Any) -> dict[str, Any]:
    query = "&".join(f"{name}={value}" for name, value in params.items())
    return api(stack, "GET", f"{path}/dashboard?{query}", token).json() or {}


def comparison_pages(stack: Stack, token: str, path: str,
                     **params: Any) -> list[dict[str, Any]]:
    """Every page of Mis Precios, following each answer's cursor."""
    pages: list[dict[str, Any]] = []
    cursor = None
    while len(pages) < 20:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        answer = api(stack, "GET", f"{path}/comparison?"
                     + "&".join(f"{name}={value}" for name, value in query.items()), token)
        page = answer.json() if answer.status == 200 else None
        if not isinstance(page, dict):
            pages.append({"status": answer.status, "body": answer.body[:200]})
            break
        pages.append(page)
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return pages


def eans(pages: list[dict[str, Any]]) -> list[str]:
    return [row["ean"] for page in pages for row in page.get("items", [])]


def entries(stack: Stack, company_id: int, since_ms: int) -> list[dict[str, Any]]:
    """What the function did with each entry of the company's branch, oldest first."""
    found = []
    paginator = stack.logs.get_paginator("filter_log_events")
    for page in paginator.paginate(
            logGroupName=f"/aws/lambda/{stack.outputs['precompute_function_name']}",
            startTime=since_ms, filterPattern=f'"{MARKER.strip()}" "{company_id}"'):
        for event in page.get("events", []):
            _, _, text = event["message"].partition(MARKER)
            try:
                line = json.loads(text)
            except json.JSONDecodeError:
                continue
            if line.get("company_id") == company_id:
                found.append((event["timestamp"], line))
    return [line for _, line in sorted(found, key=lambda pair: pair[0])]


def queue_entry(stack: Stack, company_id: int, feed_date: str) -> None:
    """An entry the way the day close queues it."""
    stack.sqs.send_message(QueueUrl=stack.outputs["precompute_queue_url"], MessageBody=json.dumps({
        "feed_date": feed_date, "company_id": company_id, "banner_id": 1, "branch_id": "1",
        "radius_km": float(CONFIGURED_RADIUS)}))


def check_wiring(checks: Checks, stack: Stack) -> bool:
    checks.section("Precompute · the function, its queue and its table")
    function = stack.outputs.get("precompute_function_name", "")
    table = stack.outputs.get("comparison_table_name", "")
    checks.expect("the stack has a precompute function and a comparison table",
                  bool(function) and bool(table), f"{function!r} {table!r}")
    if not function or not table:
        return False
    queue_arn = stack.outputs["precompute_queue_arn"]
    mappings = stack.lambda_.list_event_source_mappings(FunctionName=function)
    consumes = [mapping for mapping in mappings["EventSourceMappings"]
                if mapping["EventSourceArn"] == queue_arn]
    checks.expect("it consumes the precompute queue, at most 2 at a time, reporting "
                  "which entries failed",
                  len(consumes) == 1 and consumes[0]["State"] == "Enabled"
                  and consumes[0].get("ScalingConfig", {}).get("MaximumConcurrency") == 2
                  and "ReportBatchItemFailures" in consumes[0].get("FunctionResponseTypes", []),
                  str(mappings["EventSourceMappings"]))
    described = stack.dynamodb.describe_table(TableName=table)["Table"]
    ttl = stack.dynamodb.describe_time_to_live(TableName=table)["TimeToLiveDescription"]
    checks.expect(f"{table} is on-demand, with a TTL",
                  described.get("BillingModeSummary", {}).get("BillingMode") == "PAY_PER_REQUEST"
                  and ttl.get("TimeToLiveStatus") == "ENABLED", f"{described} {ttl}")
    return True


def check_fallback(checks: Checks, stack: Stack, token: str, path: str, day: str) -> bool:
    checks.section("Precompute · a branch without one answers live, and gets queued")
    live = dashboard(stack, token, path, feed_date=day)
    checks.expect("the tablero of the merchant's new branch answers from the live query",
                  live.get("source") == "live" and live.get("feed_date") == day
                  and (live.get("summary") or {}).get("own_assortment") == 0, str(live))
    # Asked for the same day: without one, the live answer would be (and
    # queue) the latest day of any chain.
    precomputed = wait_for(stack, lambda: (answer := dashboard(stack, token, path, feed_date=day))
                           .get("source") == "precomputed" and answer, PRECOMPUTE_TIMEOUT)
    checks.expect("the precompute it queued lands: the tablero then answers from it, "
                  "for that day", bool(precomputed) and precomputed.get("feed_date") == day,
                  str(precomputed or dashboard(stack, token, path, feed_date=day)))
    return bool(precomputed)


def check_load(checks: Checks, stack: Stack, token: str, merchant: dict[str, Any],
               path: str, day: str) -> bool:
    checks.section(f"Precompute · an upload and a rival of {day} reach the tablero")
    company = merchant["company_id"]
    started = api(stack, "POST", "/uploads", token,
                  {"feed_date": day, "filename": "verify.csv"}).json() or {}
    upload_id = started.get("upload_id")
    checks.expect("the merchant starts an upload for the day", bool(upload_id), str(started))
    if not upload_id:
        return False
    stack.prefixes.extend([f"raw/uploads/{upload_id}/", f"rejected/uploads/{upload_id}/",
                           f"curated/feed_date={day}/chain_id={company}/"])
    post_file(started, "verify.csv", price_list(), portal_origin(stack))
    done = wait_for(stack, settled(stack, token, upload_id, "done"), JOB_TIMEOUT)
    checks.expect(f"its {len(EANS)} prices load", done is not None
                  and done.get("rows_ok") == len(EANS), str(done))
    if done is None:
        return False

    previous = job(stack, token, RIVAL, day)
    put_raw(stack, RIVAL, "verify.zip", sepa_archive(
        RIVAL, RIVAL_PRICES, latitude=LATITUDE + RIVAL_NORTH, longitude=LONGITUDE), day)
    loaded = wait_for(stack, processed_after(stack, token, RIVAL, previous, "done", day),
                      JOB_TIMEOUT)
    checks.expect("the rival's chain, 500 m north, loads the same day", loaded is not None,
                  str(job(stack, token, RIVAL, day)))
    if loaded is None:
        return False

    # The rival's notice closes the day, which rebuilds the neighbors and
    # queues every followed branch.
    def compared() -> dict[str, Any] | None:
        answer = dashboard(stack, token, path)
        return answer if (answer.get("source") == "precomputed" and answer.get("feed_date") == day
                          and (answer.get("summary") or {}).get("compared") == len(EANS)) else None
    board = wait_for(stack, compared, PRECOMPUTE_TIMEOUT)
    checks.expect(f"the precomputed tablero compares the {len(EANS)} products",
                  board is not None, str(dashboard(stack, token, path)))
    if board is None:
        return False
    checks.expect("its summary: one product matches the minimum, the rest are dearer",
                  board["summary"] == {"compared": 25, "cheaper": 0, "matched_min": 1,
                                       "dearer": 24, "own_assortment": 25}, str(board["summary"]))
    worst = [row["ean"] for row in board.get("worst", [])]
    checks.expect("and its worst 20, the biggest gap first", worst == WORST_FIRST[:20],
                  str(worst))
    top = (board.get("worst") or [{}])[0]
    checks.expect("each row with the prices the files said",
                  (float(top.get("own_price", 0)), float(top.get("min_price", 0)),
                   top.get("offers")) == (100.0, 76.0, 1), str(top))
    live = api(stack, "GET", f"{path}/comparison/summary?feed_date={day}", token).json()
    checks.expect("the same summary the live query gives", board["summary"] == live, str(live))
    return True


def check_reads(checks: Checks, stack: Stack, token: str, path: str, day: str) -> None:
    checks.section("Mis Precios · pages through the precompute with a cursor")
    pages = comparison_pages(stack, token, path, limit=10)
    checks.expect("three pages of 10, 10 and 5, every one from the precompute",
                  [len(page.get("items", [])) for page in pages] == [10, 10, 5]
                  and all(page.get("source") == "precomputed" and page.get("feed_date") == day
                          for page in pages), str(pages)[:400])
    checks.expect("walking them gives the 25 products once, worst gap first",
                  eans(pages) == WORST_FIRST, str(eans(pages)))
    checks.expect("the last page has no cursor", pages[-1].get("next_cursor") is None,
                  str(pages[-1])[:200])
    filtered = comparison_pages(stack, token, path, limit=4, q=FILTER)
    checks.expect(f"filtering by description pages through its {len(FILTERED)} matches",
                  [len(page.get("items", [])) for page in filtered] == [4, 4, 2]
                  and eans(filtered) == FILTERED, str(eans(filtered)))
    tampered = api(stack, "GET", f"{path}/comparison?cursor=not-a-cursor", token)
    checks.expect("a cursor the API did not hand out is a 400", tampered.status == 400,
                  f"{tampered.status} {tampered.body[:200]}")

    checks.section("Mis Precios · another radius answers live, the same way")
    precomputed = dashboard(stack, token, path)
    live = dashboard(stack, token, path, radius_km=1)
    checks.expect("the tablero at 1 km, not the configured 5, answers live",
                  live.get("source") == "live", str(live)[:200])
    checks.expect("with the same day, summary and worst 20 (the rival is within 1 km)",
                  {key: live.get(key) for key in ("feed_date", "summary", "worst")}
                  == {key: precomputed.get(key) for key in ("feed_date", "summary", "worst")},
                  f"{live} vs {precomputed}"[:400])
    pages = comparison_pages(stack, token, path, limit=10, radius_km=1)
    checks.expect("Mis Precios at 1 km pages live with a cursor too, the same 25 in order",
                  all(page.get("source") == "live" for page in pages)
                  and eans(pages) == WORST_FIRST, str(eans(pages)))


def check_meta_moves_forward(checks: Checks, stack: Stack, token: str, company: int,
                             path: str, day: str, next_day: str, since_ms: int) -> None:
    checks.section("Precompute · META moves to a newer day, never to an older one")
    queue_entry(stack, company, next_day)
    newer = wait_for(stack, lambda: (answer := dashboard(stack, token, path))
                     .get("feed_date") == next_day and answer, PRECOMPUTE_TIMEOUT)
    checks.expect(f"an entry for {next_day} moves the tablero to that day (with nothing "
                  "to compare: the merchant loaded nothing then)",
                  bool(newer) and newer.get("source") == "precomputed"
                  and (newer.get("summary") or {}).get("own_assortment") == 0,
                  str(newer or dashboard(stack, token, path)))
    if not newer:
        return
    queue_entry(stack, company, day)
    stale = wait_for(stack, lambda: [line for line in entries(stack, company, since_ms)
                                     if line.get("feed_date") == day
                                     and line.get("outcome") == "stale"],
                     PRECOMPUTE_TIMEOUT, interval=5)
    checks.expect(f"an entry for {day}, older than META, is left alone", bool(stale),
                  str(entries(stack, company, since_ms)))
    kept = dashboard(stack, token, path)
    checks.expect(f"and the tablero stays on {next_day}",
                  kept.get("source") == "precomputed" and kept.get("feed_date") == next_day,
                  str(kept)[:200])


def check_new_radius(checks: Checks, stack: Stack, token: str, path: str, day: str) -> None:
    checks.section("Mis Precios · a radius never precomputed answers live, and gets queued")
    patched = api(stack, "PATCH", f"/me{path}", token, {"radius_km": 3})
    checks.expect("the org saves 3 km as its branch's radius", patched.status == 200,
                  f"{patched.status} {patched.body[:200]}")
    # The first read at 3 km: the tablero's reads were all at 5 km or live.
    live = comparison_pages(stack, token, path, feed_date=day)
    checks.expect("Mis Precios at 3 km answers from the live query, the 25 products in order",
                  all(page.get("source") == "live" for page in live)
                  and eans(live) == WORST_FIRST, str(live)[:300])
    landed = wait_for(stack, lambda: (pages := comparison_pages(stack, token, path, feed_date=day))
                      and all(page.get("source") == "precomputed" for page in pages) and pages,
                      PRECOMPUTE_TIMEOUT)
    checks.expect("the precompute it queued lands, with the same rows",
                  bool(landed) and eans(landed) == WORST_FIRST, str(landed)[:300])


def run(checks: Checks, stack: Stack) -> None:
    if not check_wiring(checks, stack):
        return
    token = owner_of_new_org(stack, "LGDC verify, precálculo")
    merchant = api(stack, "POST", "/me/merchant", token, {
        "name": "LGDC verify, precálculo", "province": "AR-C", "locality": "CABA",
        "latitude": LATITUDE, "longitude": LONGITUDE}).json() or {}
    company = merchant.get("company_id")
    days = free_days(stack, token)
    checks.expect("the org sets up its own branch, and the run has days of its own",
                  bool(company) and days is not None, f"{merchant} {days}")
    if not company or days is None:
        return
    day, next_day = days[0], days[1]
    checks.note(f"merchant {company}, days {day} and {next_day}")
    since_ms = int((time.time() - 60) * 1000)
    path = f"/branches/{company}/1/1"
    if not check_fallback(checks, stack, token, path, day):
        return
    if not check_load(checks, stack, token, merchant, path, day):
        return
    check_reads(checks, stack, token, path, day)
    # Last: they move META on, and then the branch's radius.
    check_meta_moves_forward(checks, stack, token, company, path, day, next_day, since_ms)
    check_new_radius(checks, stack, token, path, day)

