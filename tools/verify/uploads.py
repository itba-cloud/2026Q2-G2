"""A merchant's own price list, uploaded from the portal (ticket 08).

The way the portal does it: POST /uploads hands back a presigned POST, the
file goes straight to S3 under raw/uploads/ (from the portal's origin, which
the bucket's CORS lets the browser hear back from), and nothing else is asked
of the API. S3's notification creates the job, like for a SEPA chain's file;
the job ends in done, the upload says so with its rejected rows, its prices
are the merchant's branch's, and the Pipeline screen lists the job under the
merchant's name. A file that cannot be read fails the upload with the reason,
without a retry; another org sees none of it; and deleting the upload takes
its prices with it.

The merchant's branch sits in the South Atlantic (-50, -35), more than 500 km
from the competitors suite's branches, so it never enters their comparisons.
Every run's merchant is a new one at the same spot.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import uuid
from typing import Any

from archives import FEED_DATE, JOB_TIMEOUT
from cognito import owner_of_new_org
from harness import Checks, Response, Stack, api, fetch, wait_for

LATITUDE, LONGITUDE = -50.0, -35.0

# One unusable row out of fifty, under the 5% that fails the whole file.
PRICES = {f"77999992{index:05d}": 200 + index + 0.25 for index in range(49)}
BAD_ROW = "123,10.00,CODIGO CORTO"
MAX_BYTES = 50 * 1024 * 1024


def price_list() -> bytes:
    rows = [f"{ean},{price:.2f},PRODUCTO CARGA {ean}" for ean, price in PRICES.items()]
    return ("ean,price,description\n" + "\n".join([*rows, BAD_ROW]) + "\n").encode()


def portal_origin(stack: Stack) -> str:
    url = urllib.parse.urlsplit(stack.outputs["portal_url"])
    return f"{url.scheme}://{url.netloc}"


def post_file(started: dict[str, Any], filename: str, body: bytes, origin: str) -> Response:
    """The browser's POST: the policy's fields, then the file, as a form,
    from the portal's origin."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
             f"{value}\r\n".encode() for name, value in started["fields"].items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'.encode()
                 + body + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return fetch(started["url"], "POST", b"".join(parts), {
        "Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": origin})


def preflight(url: str, origin: str) -> Response:
    """What a browser would ask S3 before a POST from `origin`."""
    return fetch(url, "OPTIONS", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST"})


def size_cap(started: dict[str, Any]) -> int | None:
    """The largest file the signed policy lets S3 take, as the browser gets it."""
    policy = json.loads(base64.b64decode(started["fields"]["policy"]))
    caps = [condition[2] for condition in policy["conditions"]
            if isinstance(condition, list) and condition[0] == "content-length-range"]
    return caps[0] if caps else None


def upload(stack: Stack, token: str, upload_id: str) -> dict[str, Any]:
    return api(stack, "GET", f"/uploads/{upload_id}", token).json() or {}


def settled(stack: Stack, token: str, upload_id: str, status: str):
    """A condition: the upload reached `status` (done or failed)."""
    def condition() -> dict[str, Any] | None:
        current = upload(stack, token, upload_id)
        return current if current.get("status") == status else None
    return condition


def start_upload(checks: Checks, stack: Stack, token: str, filename: str) -> dict[str, Any]:
    created = api(stack, "POST", "/uploads", token,
                  {"feed_date": FEED_DATE, "filename": filename})
    body = created.json() or {}
    upload_id = body.get("upload_id")
    if upload_id:
        # Whatever the run leaves under the upload's keys, every version.
        stack.prefixes.extend([f"raw/uploads/{upload_id}/", f"rejected/uploads/{upload_id}/"])
    checks.expect(f"POST /uploads hands back a presigned POST for {filename}",
                  created.status == 201 and bool(upload_id) and bool(body.get("url"))
                  and bool((body.get("fields") or {}).get("policy")),
                  f"{created.status} {created.body[:200]}")
    return body


def set_up_merchant(checks: Checks, stack: Stack, token: str) -> dict[str, Any] | None:
    merchant = api(stack, "POST", "/me/merchant", token, {
        "name": "LGDC verify, carga", "province": "AR-C", "locality": "CABA",
        "latitude": LATITUDE, "longitude": LONGITUDE})
    body = merchant.json() or {}
    checks.expect("the org sets up its own branch", merchant.status == 200
                  and bool(body.get("company_id")), f"{merchant.status} {merchant.body[:200]}")
    if body.get("company_id"):
        stack.prefixes.append(
            f"curated/feed_date={FEED_DATE}/chain_id={body['company_id']}/")
        return body
    return None


def check_cors(checks: Checks, stack: Stack, url: str, posted: Response) -> None:
    checks.section("Upload · straight from the portal's origin to S3")
    origin = portal_origin(stack)
    checks.expect("the POST straight to S3 succeeds", posted.status == 204,
                  f"{posted.status} {posted.body[:200]}")
    checks.expect("and its answer is the portal's to read (CORS)",
                  posted.headers.get("access-control-allow-origin") == origin,
                  f"{posted.headers}")
    refused = preflight(url, "https://example.com")
    checks.expect("another origin is not allowed",
                  "access-control-allow-origin" not in refused.headers,
                  f"{refused.status} {refused.headers}")


def check_load(checks: Checks, stack: Stack, token: str,
               merchant: dict[str, Any]) -> dict[str, Any] | None:
    checks.section("Upload · a CSV in raw/uploads/ becomes the merchant's prices")
    started = start_upload(checks, stack, token, "verify.csv")
    if not started.get("url"):
        return None
    upload_id = started["upload_id"]
    key = started["fields"].get("key", "")
    checks.expect("it writes the one key raw/uploads/<upload_id>/verify.csv",
                  key == f"raw/uploads/{upload_id}/verify.csv", key)
    checks.expect("and caps the file at 50 MB", size_cap(started) == MAX_BYTES,
                  str(size_cap(started)))
    checks.expect("the upload waits for its file",
                  upload(stack, token, upload_id).get("status") == "awaiting_file",
                  str(upload(stack, token, upload_id)))

    posted = post_file(started, "verify.csv", price_list(), portal_origin(stack))
    check_cors(checks, stack, started["url"], posted)

    # Nothing else is asked of the API: S3's notification makes the job.
    gone = api(stack, "POST", f"/uploads/{upload_id}/complete", token)
    checks.expect("the API has no enqueue step any more", gone.status in (404, 405),
                  f"{gone.status} {gone.body[:200]}")

    done = wait_for(stack, settled(stack, token, upload_id, "done"), JOB_TIMEOUT)
    checks.expect("the upload ends in done, with no call after the POST", done is not None,
                  f"last seen: {upload(stack, token, upload_id)}")
    if done is None:
        return None
    checks.expect("its job is linked and done",
                  bool(done.get("job_id")) and done.get("job_status") == "done", str(done))
    checks.expect(f"it counted {len(PRICES)} prices and 1 rejected row",
                  (done.get("rows_ok"), done.get("rows_rejected")) == (len(PRICES), 1), str(done))

    rejected = api(stack, "GET", f"/uploads/{upload_id}/rejected", token)
    rows = rejected.json() if rejected.status == 200 else None
    checks.expect("its rejected row says which line and why",
                  isinstance(rows, list) and len(rows) == 1 and rows[0].get("line") == len(PRICES) + 2
                  and bool(rows[0].get("reason")) and "123" in rows[0].get("content", ""),
                  f"{rejected.status} {rejected.body[:300]}")

    listed = api(stack, "GET", "/uploads", token).json() or []
    mine = next((row for row in listed if row.get("upload_id") == upload_id), {})
    checks.expect("the upload list shows it done with its counts",
                  (mine.get("status"), mine.get("rows_ok"), mine.get("rows_rejected"))
                  == ("done", len(PRICES), 1), str(mine))

    path = f"/branches/{merchant['company_id']}/1/1"
    prices = api(stack, "GET", f"{path}/prices?feed_date={FEED_DATE}&limit=500", token).json() or []
    by_ean = {row["ean"]: float(row["price"]) for row in prices}
    checks.expect(f"the API serves its {len(PRICES)} prices on the merchant's branch",
                  len(prices) == len(PRICES), f"got {len(prices)}")
    checks.expect("with the price the file said",
                  by_ean.get("7799999200007") == 207.25, f"got {by_ean.get('7799999200007')}")
    dashboard = api(stack, "GET", f"{path}/dashboard?feed_date={FEED_DATE}", token).json() or {}
    checks.expect("the tablero counts them as the branch's own assortment",
                  (dashboard.get("summary") or {}).get("own_assortment") == len(PRICES),
                  str(dashboard.get("summary")))

    jobs = api(stack, "GET", f"/ingest/jobs?feed_date={FEED_DATE}&limit=500", token).json() or []
    job = next((row for row in jobs if row.get("job_id") == done["job_id"]), {})
    checks.expect("the Pipeline screen lists the job for its day and the merchant's chain",
                  job.get("kind") == "upload" and job.get("status") == "done"
                  and job.get("feed_date") == FEED_DATE
                  and job.get("chain_id") == str(merchant["company_id"])
                  and job.get("chain_name") == "LGDC verify, carga", str(job))
    return done


def check_broken(checks: Checks, stack: Stack, token: str) -> None:
    checks.section("Upload · a file that cannot be read fails, without a retry")
    started = start_upload(checks, stack, token, "broken.csv")
    if not started.get("url"):
        return
    upload_id = started["upload_id"]
    post_file(started, "broken.csv", b"codigo;importe\n1;2\n", portal_origin(stack))
    failed = wait_for(stack, settled(stack, token, upload_id, "failed"), JOB_TIMEOUT)
    checks.expect("the upload ends in failed", failed is not None,
                  f"last seen: {upload(stack, token, upload_id)}")
    if failed is None:
        return
    checks.expect("with the reason", "missing columns" in (failed.get("last_error") or ""),
                  str(failed))
    jobs = api(stack, "GET", f"/ingest/jobs?feed_date={FEED_DATE}&limit=500", token).json() or []
    job = next((row for row in jobs if row.get("job_id") == failed.get("job_id")), {})
    checks.expect("its job was tried once", job.get("attempts") == 1, str(job))


def check_isolation(checks: Checks, stack: Stack, upload_id: str) -> None:
    checks.section("Upload · another org sees none of it")
    other = owner_of_new_org(stack, "LGDC verify, otra carga")
    listed = api(stack, "GET", "/uploads", other).json()
    checks.expect("its upload list is empty", listed == [], str(listed))
    refused = {path: api(stack, "GET", path, other).status
               for path in (f"/uploads/{upload_id}", f"/uploads/{upload_id}/rejected")}
    checks.expect("the upload and its rejected rows are 404 for it",
                  all(status == 404 for status in refused.values()), str(refused))
    deleted = api(stack, "DELETE", f"/uploads/{upload_id}", other)
    checks.expect("and it cannot delete it (404)", deleted.status == 404, f"{deleted.status}")


def check_delete(checks: Checks, stack: Stack, token: str, merchant: dict[str, Any],
                 upload_id: str) -> None:
    checks.section("Upload · deleting it takes its prices")
    deleted = api(stack, "DELETE", f"/uploads/{upload_id}", token)
    checks.expect(f"DELETE /uploads/{{id}} removes its {len(PRICES)} prices",
                  deleted.status == 200 and (deleted.json() or {}).get("precios") == len(PRICES),
                  f"{deleted.status} {deleted.body[:200]}")
    prices = api(stack, "GET", f"/branches/{merchant['company_id']}/1/1/prices"
                               f"?feed_date={FEED_DATE}", token).json()
    checks.expect("the branch has no prices left that day", prices == [], str(prices)[:200])


def run(checks: Checks, stack: Stack) -> None:
    checks.section("Upload · the merchant's own branch")
    token = owner_of_new_org(stack, "LGDC verify, carga")
    merchant = set_up_merchant(checks, stack, token)
    if merchant is None:
        return
    done = check_load(checks, stack, token, merchant)
    check_broken(checks, stack, token)
    if done is None:
        return
    check_isolation(checks, stack, done["upload_id"])
    check_delete(checks, stack, token, merchant, done["upload_id"])
