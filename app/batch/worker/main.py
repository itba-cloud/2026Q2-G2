"""Parse worker.

Takes a job from the jobs queue, reads the chain archive it points at, and
turns it into rows: prices and branches into the database, unusable rows into
`rejected/`, the validated set into `curated/` as Parquet.

A job is a message S3 sent when a file landed in `raw/`: a chain's archive
from the ingest, or a merchant's own price list, which the portal uploads to
`raw/uploads/<upload_id>/` with a presigned URL. Several workers run at once
and none coordinates with the others: SQS hands each message to one of them,
and the inbox (`processed_message`) makes a message delivered twice load only
once.

A job that settles, loaded or failed for good, is announced on the
cadena-procesada topic through the outbox (outbox.py): the alerts, the day
close and the admin's email hear about it from there.

    PYTHONPATH=app/shared python app/batch/worker/main.py
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import signal
import socket
import time
import urllib.parse
from typing import Any

import pyarrow
import pyarrow.parquet

import outbox
import sepa
import upload
# `common` lives in app/shared: next to this file in the workers' image, on
# PYTHONPATH when run from the repo.
from common import SETTINGS, aws_client, configure_logging, connect

log = logging.getLogger("worker")

WORKER_ID = os.getenv("WORKER_ID") or f"worker-{socket.gethostname()}"
QUEUE_URL = os.getenv("JOBS_QUEUE_URL", "")
TOPIC_ARN = os.getenv("CHAIN_PROCESSED_TOPIC_ARN", "")
# The queue's maxReceiveCount: on that attempt a failure is final, and the
# message moves to the DLQ instead of coming back.
MAX_RECEIVE_COUNT = int(os.getenv("MAX_RECEIVE_COUNT", "3"))
# How long a job that failed for a transient reason waits before its retry.
RETRY_DELAY_SECONDS = int(os.getenv("RETRY_DELAY_SECONDS", "30"))
# Above this share of unusable rows we assume the chain changed its format and
# fail the whole file, rather than loading a fraction of it as if it were fine.
REJECT_THRESHOLD = float(os.getenv("REJECT_THRESHOLD", "0.05"))
ROW_LIMIT = int(os.getenv("MAX_ROWS_PER_JOB", "0"))
# Below this, a price move is rounding noise, not a change worth an alert.
CHANGE_FLOOR_PCT = float(os.getenv("CHANGE_FLOOR_PCT", "0.5"))
MAX_REJECTS_STORED = 5_000
# Long polling: a receive waits this long for a message before coming back
# empty. Under the 30 s ECS gives a task between SIGTERM and SIGKILL.
WAIT_SECONDS = 20

SCRATCH = "/tmp"

# Where the ingest leaves a chain's file. The key is the job's identity.
RAW_KEY = re.compile(r"^raw/feed_date=(\d{4}-\d{2}-\d{2})/chain_id=(\d+)/[^/]+$")
# Where the portal uploads a merchant's price list (POST /uploads). The key
# names the upload; its row in `price_upload` says whose and for which day.
UPLOAD_KEY = re.compile(r"^raw/uploads/([0-9a-f-]{36})/[^/]+$")

# The worker records the job when it takes the message, and only then: the
# queue, not this table, says what is left to do. One row per chain and day,
# so a reprocess (a new version of the file) reuses its row.
#
# Not when the message is already in the inbox: a redelivery must not put a
# finished job back in `processing`. The check is in the same statement as
# the update, so a delivery that races the one loading the file either sees
# its commit here or waits for it on the inbox row inside load().
REGISTER = """
    INSERT INTO ingest_job (feed_date, chain_id, s3_key, status, worker_id,
                            claimed_at, attempts)
    VALUES (%(feed_date)s, %(chain_id)s, %(s3_key)s, 'processing', %(worker_id)s,
            now(), %(attempt)s)
    ON CONFLICT (feed_date, chain_id) WHERE kind = 'sepa' DO UPDATE SET
        s3_key = EXCLUDED.s3_key, status = 'processing', worker_id = EXCLUDED.worker_id,
        claimed_at = now(), attempts = EXCLUDED.attempts, rows_ok = NULL,
        rows_rejected = NULL, last_error = NULL, updated_at = now()
    WHERE NOT EXISTS (SELECT 1 FROM processed_message
                      WHERE message_key = %(message_key)s)
    RETURNING job_id
"""

# The inbox. Inside the load's transaction: the prices and the message are
# recorded together or not at all.
RECORD_MESSAGE = """
    INSERT INTO processed_message (message_key) VALUES (%s)
    ON CONFLICT (message_key) DO NOTHING
    RETURNING message_key
"""

FINISH = """
    UPDATE ingest_job
    SET status = 'done', rows_ok = %s, rows_rejected = %s, last_error = NULL,
        updated_at = now()
    WHERE job_id = %s
"""

FAIL = """
    UPDATE ingest_job
    SET status = 'failed', last_error = %s, updated_at = now()
    WHERE job_id = %s
"""

# A transient failure with attempts left: the message comes back, and the
# Pipeline screen shows the job waiting with the error that held it.
RETRY = """
    UPDATE ingest_job
    SET status = 'pending', last_error = %s, updated_at = now()
    WHERE job_id = %s
"""

# An upload's job: one per upload, not per chain and day, since a merchant
# may upload more than one list for the same day. The upload row is locked
# first, so two deliveries of its file take turns here.
UPLOAD_ROW = """
    SELECT company_id, feed_date, job_id FROM price_upload
    WHERE upload_id = %s AND s3_key = %s
    FOR UPDATE
"""

IN_INBOX = "SELECT 1 FROM processed_message WHERE message_key = %s"

REGISTER_UPLOAD = """
    INSERT INTO ingest_job (feed_date, chain_id, s3_key, kind, status, worker_id,
                            claimed_at, attempts)
    VALUES (%(feed_date)s, %(chain_id)s, %(s3_key)s, 'upload', 'processing',
            %(worker_id)s, now(), %(attempt)s)
    RETURNING job_id
"""

# A new version of the same file (the same key written again) reuses the job.
REREGISTER_UPLOAD = """
    UPDATE ingest_job
    SET status = 'processing', worker_id = %(worker_id)s, claimed_at = now(),
        attempts = %(attempt)s, rows_ok = NULL, rows_rejected = NULL,
        last_error = NULL, updated_at = now()
    WHERE job_id = %(job_id)s
"""

# The file arrived and has its job: what the portal shows until it settles.
LINK_UPLOAD = """
    UPDATE price_upload SET status = 'queued', job_id = %s, updated_at = now()
    WHERE upload_id = %s
"""

# `price_upload` is the merchant's own view of the job: it closes with it.
# Harmless for SEPA jobs: no `price_upload` row points at them.
CLOSE_UPLOAD = """
    UPDATE price_upload SET status = %s, updated_at = now()
    WHERE job_id = ANY(%s)
"""


class AlreadyProcessed(Exception):
    """The message is in the inbox: another delivery already loaded it."""


@dataclasses.dataclass(frozen=True)
class Job:
    job_id: int
    s3_key: str
    version_id: str
    feed_date: str
    chain_id: str
    kind: str = "sepa"
    # Which delivery of the message this is (SQS's ApproximateReceiveCount).
    attempt: int = 1
    # An upload's job only: the `price_upload` row it loads.
    upload_id: str | None = None

    @property
    def partition(self) -> str:
        return f"feed_date={self.feed_date}/chain_id={self.chain_id}"

    @property
    def rejected_prefix(self) -> str:
        """Where its rejected rows (or the file, if broken) go. An upload has
        its own, so the portal shows each upload's rows, not its day's last."""
        if self.upload_id:
            return f"rejected/uploads/{self.upload_id}"
        return f"rejected/{self.partition}"

    @property
    def filename(self) -> str:
        return self.s3_key.rsplit("/", 1)[-1]

    @property
    def message_key(self) -> str:
        """The inbox key. A reprocess uploads a new version, so it is a new
        message; a redelivery of the same version is not."""
        return f"{self.s3_key}#{self.version_id}"

    def notice(self, status: str, price_changes: int = 0) -> outbox.ChainProcessed:
        """What the topic hears about this job once it settles."""
        return outbox.ChainProcessed(self.feed_date, int(self.chain_id), self.kind,
                                     status, price_changes)


def s3_object(body: str) -> tuple[str, str] | None:
    """The key and version of the object an S3 notification is about, or None
    for a message that is not a new object (the test event S3 sends when the
    notification is created)."""
    records = json.loads(body).get("Records") or []
    if len(records) != 1 or not records[0].get("eventName", "").startswith("ObjectCreated"):
        return None
    created = records[0]["s3"]["object"]
    # S3 sends the key URL-encoded, spaces as '+'.
    return urllib.parse.unquote_plus(created["key"]), created.get("versionId", "null")


def register(connection: Any, key: str, version_id: str, attempt: int) -> Job | None:
    """Record the job a message is about, or None if the key is neither a
    chain's file nor a known upload. Raises AlreadyProcessed if the message
    is in the inbox."""
    if upload := UPLOAD_KEY.match(key):
        return register_upload(connection, key, version_id, attempt, upload.group(1))
    match = RAW_KEY.match(key)
    if match is None:
        return None
    feed_date, chain_id = match.groups()
    job = Job(0, key, version_id, feed_date, chain_id, attempt=attempt)
    row = connection.execute(REGISTER, {
        "feed_date": feed_date, "chain_id": chain_id, "s3_key": key,
        "worker_id": WORKER_ID, "attempt": attempt, "message_key": job.message_key,
    }).fetchone()
    if row is None:
        raise AlreadyProcessed(job.message_key)
    return dataclasses.replace(job, job_id=row["job_id"])


def register_upload(connection: Any, key: str, version_id: str, attempt: int,
                    upload_id: str) -> Job | None:
    """Record an upload's job and link it to the upload. None if no upload
    has this key (deleted before its file was processed)."""
    with connection.transaction():
        row = connection.execute(UPLOAD_ROW, (upload_id, key)).fetchone()
        if row is None:
            return None
        job = Job(0, key, version_id, str(row["feed_date"]), str(row["company_id"]),
                  kind="upload", attempt=attempt, upload_id=upload_id)
        if connection.execute(IN_INBOX, (job.message_key,)).fetchone() is not None:
            raise AlreadyProcessed(job.message_key)
        params = {"feed_date": job.feed_date, "chain_id": job.chain_id, "s3_key": key,
                  "worker_id": WORKER_ID, "attempt": attempt, "job_id": row["job_id"]}
        if row["job_id"] is None:
            job_id = connection.execute(REGISTER_UPLOAD, params).fetchone()["job_id"]
        else:
            connection.execute(REREGISTER_UPLOAD, params)
            job_id = row["job_id"]
        connection.execute(LINK_UPLOAD, (job_id, upload_id))
    return dataclasses.replace(job, job_id=job_id)


def close_uploads(connection: Any, status: str, job_ids: list[int]) -> None:
    """Record a job's outcome on the `price_upload` row that submitted it."""
    if job_ids:
        connection.execute(CLOSE_UPLOAD, (status, job_ids))


def download(s3: Any, job: Job) -> str:
    """Bring the source file to local disk: the version the message is about,
    not whatever the key holds now. The disk is scratch: it dies with the
    task, and nothing durable is kept here."""
    suffix = "zip" if job.kind == "sepa" else "csv"
    destination = f"{SCRATCH}/job-{job.job_id}.{suffix}"
    s3.download_file(SETTINGS.bucket, job.s3_key, destination,
                     ExtraArgs={"VersionId": job.version_id})
    return destination


def upload_target(connection: Any, company_id: int) -> tuple[int, str]:
    """The banner and branch id an upload's rows belong to. A merchant has
    exactly one branch, created once at registration by the backend -- the
    file itself carries no branch information to read."""
    row = connection.execute(
        "SELECT banner_id, branch_id FROM merchant_source WHERE company_id = %s",
        (company_id,)).fetchone()
    if row is None:
        raise sepa.SepaFormatError(f"no merchant registered for company {company_id}")
    return row["banner_id"], row["branch_id"]


def store_rejects(s3: Any, job: Job, rejected: list[sepa.Rejected]) -> None:
    """One JSON object per dropped row: which line, why, and what it said.

    Enough to hand back to the chain, or to spot that a format changed.
    """
    if not rejected:
        return
    body = "\n".join(
        json.dumps({"line": row.line_number, "reason": row.reason,
                    "content": row.content}, ensure_ascii=False)
        for row in rejected[:MAX_REJECTS_STORED])
    s3.put_object(Bucket=SETTINGS.bucket,
                  Key=f"{job.rejected_prefix}/rejected.jsonl",
                  Body=body.encode())


def store_curated(s3: Any, job: Job, prices: list[sepa.Price]) -> None:
    """The validated rows, as Parquet, partitioned the same way as the raw file.

    Columnar and compressed, so a report spanning months can be answered from
    S3 without going through the database.
    """
    if not prices:
        return
    table = pyarrow.table({
        "ean": [p.ean for p in prices],
        "company_id": [p.company_id for p in prices],
        "banner_id": [p.banner_id for p in prices],
        "branch_id": [p.branch_id for p in prices],
        "price": [p.amount for p in prices],
    })
    path = f"{SCRATCH}/job-{job.job_id}.parquet"
    pyarrow.parquet.write_table(table, path, compression="snappy")

    with open(path, "rb") as parquet:
        s3.put_object(Bucket=SETTINGS.bucket,
                      Key=f"curated/{job.partition}/prices.parquet", Body=parquet)
    os.remove(path)


# The description most chains agree on, recomputed only for the EANs this job
# touched. Last-writer-wins (the original rule) would let whichever chain
# re-ingests last rename a product every other chain also sells; this reads
# every chain's own wording back from product_name instead of trusting one.
CANONICAL_PRODUCT = """
    WITH touched AS (SELECT DISTINCT ean FROM staged_product),
         tally AS (
            SELECT v.ean, v.description, v.brand, v.presentation, count(*) AS n
            FROM product_name v JOIN touched USING (ean)
            GROUP BY v.ean, v.description, v.brand, v.presentation
         ),
         ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY ean ORDER BY n DESC, description) AS rank
            FROM tally
         )
    INSERT INTO product (ean, description, brand, presentation)
    SELECT ean, description, brand, presentation FROM ranked WHERE rank = 1
    ON CONFLICT (ean) DO UPDATE SET
        description = EXCLUDED.description, brand = EXCLUDED.brand,
        presentation = EXCLUDED.presentation
"""

DETECT_CHANGES = """
    INSERT INTO price_change
    SELECT p.feed_date, p.ean, p.company_id, p.banner_id, p.branch_id,
           q.feed_date, q.price, p.price,
           round(100 * (p.price - q.price) / q.price, 2)
    FROM price p
    JOIN price q
      ON  q.feed_date  = %s
      AND q.ean        = p.ean
      AND q.company_id = p.company_id
      AND q.banner_id  = p.banner_id
      AND q.branch_id  = p.branch_id
    WHERE p.feed_date  = %s
      AND p.company_id = %s
      AND q.price > 0
      AND p.price <> q.price
      AND abs(100 * (p.price - q.price) / q.price) >= %s
"""

def detect_changes(cursor: Any, feed_date: str, company_id: int) -> int:
    """One row in price_change per (branch, product) whose price moved since
    this chain's previous load; how many. A no-op on a chain's first-ever
    day: there is nothing yet to compare against.

    Reads the previous date from ingest_job rather than MAX(feed_date) on
    price itself, which has no supporting index and would scan.
    """
    previous = cursor.execute(
        "SELECT max(feed_date) AS previous FROM ingest_job "
        "WHERE chain_id = %s AND feed_date < %s AND status = 'done'",
        (str(company_id), feed_date)).fetchone()["previous"]
    if previous is None:
        return 0
    cursor.execute(DETECT_CHANGES, (previous, feed_date, company_id, CHANGE_FLOOR_PCT))
    return cursor.rowcount


def load(connection: Any, job: Job, contents: sepa.Contents) -> None:
    """Write the parsed rows into the catalogue, in one transaction.

    The same transaction records the message in the inbox, closes the job
    and stores its notice, with how many prices changed, in the outbox: if
    the message was already there, nothing is written and AlreadyProcessed
    says so. The alerts are not raised here: the alert-matcher Lambda raises
    them when the notice reaches it.

    Reprocessing a chain's day replaces that day's prices rather than adding
    to them, so running a job twice leaves the same rows behind -- and so
    does everything derived from those prices: the per-chain catalogue
    entries, the detected price changes, and the alerts they triggered, which
    the alert-matcher raises again from the new changes.
    """
    company_id = int(job.chain_id)
    with connection.transaction(), connection.cursor() as cursor:
        # First, so a concurrent delivery of the same message waits on this
        # row instead of loading the same prices alongside.
        if cursor.execute(RECORD_MESSAGE, (job.message_key,)).fetchone() is None:
            raise AlreadyProcessed(job.message_key)

        cursor.execute("CREATE TEMP TABLE staged_branch (LIKE branch) ON COMMIT DROP")
        cursor.execute("CREATE TEMP TABLE staged_product (LIKE product) ON COMMIT DROP")
        cursor.execute("CREATE TEMP TABLE staged_price (LIKE price) ON COMMIT DROP")

        with cursor.copy("COPY staged_branch FROM STDIN") as copy:
            for branch in contents.branches:
                copy.write_row((
                    branch.company_id, branch.banner_id, branch.branch_id,
                    branch.banner_name, branch.legal_name, branch.name, branch.kind,
                    branch.province, branch.locality, branch.latitude, branch.longitude))

        with cursor.copy("COPY staged_product FROM STDIN") as copy:
            for product in contents.products:
                copy.write_row((product.ean, product.description,
                                product.brand, product.presentation))

        with cursor.copy("COPY staged_price FROM STDIN") as copy:
            for price in contents.prices:
                copy.write_row((price.feed_date, price.ean, price.company_id,
                                price.banner_id, price.branch_id, price.amount))

        # A chain may list the same branch or barcode more than once; DISTINCT ON
        # picks one so the upsert does not hit the same key twice per statement.
        cursor.execute("""
            INSERT INTO branch
            SELECT DISTINCT ON (company_id, banner_id, branch_id) * FROM staged_branch
            ON CONFLICT (company_id, banner_id, branch_id) DO UPDATE SET
                name = EXCLUDED.name, province = EXCLUDED.province,
                locality = EXCLUDED.locality, banner_name = EXCLUDED.banner_name
        """)

        # This chain's own wording survives in product_name even after the
        # canonical row picks someone else's; that is what lets a product's
        # page show every chain's own name for the same barcode.
        cursor.execute("""
            INSERT INTO product_name (ean, company_id, description, brand, presentation)
            SELECT DISTINCT ON (ean) ean, %s, description, brand, presentation
            FROM staged_product
            ON CONFLICT (ean, company_id) DO UPDATE SET
                description = EXCLUDED.description, brand = EXCLUDED.brand,
                presentation = EXCLUDED.presentation
        """, (company_id,))
        cursor.execute(CANONICAL_PRODUCT)

        # Everything derived from this chain's prices for this day, deleted in
        # dependency order, so a reprocess leaves no stale alert or delta.
        cursor.execute(
            "DELETE FROM alert WHERE feed_date = %s AND company_id = %s",
            (job.feed_date, company_id))
        cursor.execute(
            "DELETE FROM price_change WHERE feed_date = %s AND company_id = %s",
            (job.feed_date, company_id))
        cursor.execute(
            "DELETE FROM price WHERE feed_date = %s AND company_id = %s",
            (job.feed_date, company_id))
        cursor.execute("""
            INSERT INTO price
            SELECT DISTINCT ON (feed_date, ean, company_id, banner_id, branch_id) *
            FROM staged_price
        """)

        price_changes = detect_changes(cursor, job.feed_date, company_id)

        cursor.execute(FINISH, (len(contents.prices), len(contents.rejected), job.job_id))
        cursor.execute(CLOSE_UPLOAD, ("done", [job.job_id]))
        outbox.record(cursor, job.notice("done", price_changes))


def process(s3: Any, connection: Any, job: Job) -> sepa.Contents:
    """Do the work for one job. Raises SepaFormatError if the file is unusable."""
    archive_path = download(s3, job)
    try:
        if job.kind == "upload":
            banner_id, branch_id = upload_target(connection, int(job.chain_id))
            contents = upload.read(archive_path, job.feed_date, int(job.chain_id),
                                   banner_id, branch_id, ROW_LIMIT)
        else:
            contents = sepa.read(archive_path, job.feed_date, ROW_LIMIT)
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)

    # Rejects are stored even when the job then fails: that file is the evidence
    # for why it failed.
    store_rejects(s3, job, contents.rejected)

    if contents.rejection_ratio > REJECT_THRESHOLD:
        raise sepa.SepaFormatError(
            f"{contents.rejection_ratio:.1%} of rows rejected, over the "
            f"{REJECT_THRESHOLD:.0%} threshold")

    store_curated(s3, job, contents.prices)
    load(connection, job, contents)
    return contents


def reject(s3: Any, connection: Any, job: Job, error: Exception) -> None:
    """A broken file: retrying cannot fix it. Its copy goes to rejected/, the
    job fails with its notice, and the message is recorded as processed, so a
    redelivery does not try again. Nothing, if another delivery recorded it
    first: that one settled the job."""
    s3.copy_object(
        Bucket=SETTINGS.bucket, Key=f"{job.rejected_prefix}/{job.filename}",
        CopySource={"Bucket": SETTINGS.bucket, "Key": job.s3_key,
                    "VersionId": job.version_id})
    with connection.transaction():
        if connection.execute(RECORD_MESSAGE, (job.message_key,)).fetchone() is None:
            return
        fail(connection, job, str(error))


def fail(connection: Any, job: Job, reason: str) -> None:
    """The job failed for good: so does its upload, if it is one, and the
    topic hears about it. Inside the caller's transaction."""
    connection.execute(FAIL, (reason[:500], job.job_id))
    close_uploads(connection, "failed", [job.job_id])
    outbox.record(connection, job.notice("failed"))


def retry_later(sqs: Any, connection: Any, message: dict[str, Any], job: Job,
                error: Exception) -> None:
    """A transient failure. The message is left in the queue: it comes back
    after RETRY_DELAY_SECONDS, and after MAX_RECEIVE_COUNT deliveries SQS moves it
    to the DLQ instead. On that last attempt the job fails for good."""
    last = job.attempt >= MAX_RECEIVE_COUNT
    reason = f"attempt {job.attempt} of {MAX_RECEIVE_COUNT}: {error}"[:500]
    # Back to the queue first: it must not depend on the database answering.
    sqs.change_message_visibility(
        QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"],
        VisibilityTimeout=0 if last else RETRY_DELAY_SECONDS)
    if not last:
        connection.execute(RETRY, (reason, job.job_id))
        return
    with connection.transaction():
        fail(connection, job, reason)


def handle(s3: Any, sqs: Any, connection: Any, message: dict[str, Any]) -> None:
    """Process one message.

    Deletes the message when it is settled (loaded, a broken file, a
    duplicate, not a job at all) and leaves it in the queue when a retry may
    still work."""
    def settle() -> None:
        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])

    created = s3_object(message["Body"])
    if created is None:
        log.info("not a new object, dropped: %s", message["Body"][:200])
        settle()
        return

    key, version_id = created
    attempt = int(message["Attributes"]["ApproximateReceiveCount"])
    try:
        job = register(connection, key, version_id, attempt)
    except AlreadyProcessed:
        log.info("already processed, dropped: %s#%s", key, version_id)
        settle()
        return
    if job is None:
        log.warning("neither a chain's file nor a known upload, dropped: %s", key)
        settle()
        return

    started = time.monotonic()
    log.info("job %d, chain %s, attempt %d", job.job_id, job.chain_id, attempt)
    try:
        contents = process(s3, connection, job)
    except AlreadyProcessed:
        log.info("job %d: another delivery loaded it first", job.job_id)
        settle()
        return
    except sepa.SepaFormatError as error:
        reject(s3, connection, job, error)
        settle()
        log.error("job %d rejected: %s", job.job_id, error)
        return
    except Exception as error:
        retry_later(sqs, connection, message, job, error)
        log.exception("job %d, attempt %d of %d failed", job.job_id, attempt, MAX_RECEIVE_COUNT)
        return

    settle()
    log.info("job %d done · chain %s · branches=%d prices=%d rejected=%d · %.1fs",
             job.job_id, job.chain_id, len(contents.branches), len(contents.prices),
             len(contents.rejected), time.monotonic() - started)


def publish_notices(connection: Any, sns: Any) -> None:
    """Publish what the outbox holds. A failure is logged, not raised: the
    notices stay in the outbox, and the next pass -- after the next message,
    or the next empty receive -- tries again."""
    try:
        sent = outbox.publish_pending(connection, sns, TOPIC_ARN)
    except Exception:
        log.exception("publishing the outbox failed; its notices wait for the next try")
        return
    if sent:
        log.info("%d notice(s) published", sent)


def run(s3: Any, sqs: Any, sns: Any, connection: Any) -> None:
    """Take messages until ECS stops the task. Scaling to zero is the
    service's job, not this loop's: it stops a task only once the queue has
    been empty for a while.

    After every receive, what the jobs settled goes out through the outbox:
    each settled job's notice right after its commit, and on an empty receive
    whatever an earlier publish left behind."""
    stopping = False

    def stop(signum: int, frame: Any) -> None:
        nonlocal stopping
        stopping = True
        log.info("SIGTERM, stopping after the current receive")

    signal.signal(signal.SIGTERM, stop)

    while not stopping:
        messages = sqs.receive_message(
            QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=WAIT_SECONDS,
            AttributeNames=["ApproximateReceiveCount"]).get("Messages", [])
        for message in messages:
            handle(s3, sqs, connection, message)
        publish_notices(connection, sns)


def main() -> int:
    configure_logging()
    if not QUEUE_URL:
        raise SystemExit("JOBS_QUEUE_URL is not set")
    if not TOPIC_ARN:
        raise SystemExit("CHAIN_PROCESSED_TOPIC_ARN is not set")
    log.info("%s starting", WORKER_ID)
    # An exception escaping run() ends the task; ECS starts another, and any
    # message it held comes back once its visibility timeout runs out.
    run(aws_client("s3"), aws_client("sqs"), aws_client("sns"), connect())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
