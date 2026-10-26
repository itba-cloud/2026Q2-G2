"""Daily SEPA ingestion.

Downloads the archive the SEPA publishes for the day through the group's
proxy, and stores one object per retail chain in raw/. That is all it does:
S3 announces each new object to the jobs queue, and the workers take it from
there. It parses nothing and touches no database.

A Lambda outside the VPC (it needs the internet to reach the proxy), invoked
by a daily EventBridge cron and by the run-ingest workflow (`make ingest`).
From the repo:

    SEPA_API_URL=https://<tunnel> S3_DATA_BUCKET=<bucket> \\
        PYTHONPATH=app/shared python app/batch/ingest/main.py
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# `common` lives in app/shared: next to this file in the Lambda package, on
# PYTHONPATH when run from the repo.
from common import SETTINGS, ClientError, aws_client, configure_logging

log = logging.getLogger("ingest")

# The SEPA refuses AWS's addresses, so the archive comes through the group's
# proxy (tools/sepa-proxy), which runs outside AWS behind a tunnel. Its base
# URL, without /v1/sepa/precios. No default: without it the ingestion fails,
# and only the ingestion. The cron uses this; run-ingest may pass its own.
SEPA_API_URL = os.getenv("SEPA_API_URL", "")
# Which of the proxy's rotating weekday files to ask for. Empty takes whatever
# it considers most recent; a value here is passed to it as-is (`?dia=`), so it
# has to be spelled the way the proxy expects.
SEPA_DIA = os.getenv("SEPA_DIA", "")
LOCAL_ARCHIVE = os.getenv("CACHE_ZIP", "")
# Empty takes whatever day ends up inside the downloaded archive.
TARGET_DATE = os.getenv("FEED_DATE", "")
REPROCESS = os.getenv("REPROCESS", "").lower() in {"1", "true", "yes"}
CHAIN_LIMIT = int(os.getenv("MAX_CHAINS", "0"))

DOWNLOAD_PATH = "/tmp/sepa.zip"
CHUNK_SIZE = 1 << 20
DOWNLOAD_TIMEOUT = 120

# Inner archives are named sepa_1_comercio-sepa-9_2026-08-28_09-05-10.zip
CHAIN_FILENAME = re.compile(r"comercio-sepa-(\d+)_(\d{4}-\d{2}-\d{2})")

PROXY_HINT = ("The ingestion downloads the SEPA archive through the group's proxy "
              "(tools/sepa-proxy), which runs outside AWS behind a tunnel")


class IngestError(RuntimeError):
    """The daily archive is missing or unusable."""


@dataclass(frozen=True)
class Chain:
    """One retail chain's file inside the daily archive."""

    entry: zipfile.ZipInfo
    number: str
    date: str

    @property
    def s3_key(self) -> str:
        """Derived from the date, the chain and the file's name as the SEPA
        publishes it: the same file always lands on the same key."""
        filename = Path(self.entry.filename).name
        return f"raw/feed_date={self.date}/chain_id={self.number}/{filename}"

    @property
    def size(self) -> int:
        return self.entry.file_size


@dataclass
class IngestSummary:
    """What a run did: what run-ingest prints."""

    feed_dates: list[str] = field(default_factory=list)
    chains: int = 0
    stored: int = 0
    skipped: int = 0


def archive_url(sepa_api_url: str) -> str:
    """Where to stream the daily zip from: the proxy's most recent file, or
    the weekday SEPA_DIA names."""
    if not sepa_api_url:
        raise IngestError(
            f"SEPA_API_URL is not set. {PROXY_HINT}: start the proxy and its "
            "tunnel, and load the tunnel's URL as the SEPA_API_URL secret.")
    query = f"?dia={urllib.parse.quote(SEPA_DIA)}" if SEPA_DIA else ""
    return f"{sepa_api_url.rstrip('/')}/v1/sepa/precios{query}"


def download(url: str, destination: str = DOWNLOAD_PATH) -> str:
    """Stream the archive to disk. Any failure to get it is the proxy's: it
    says which URL and why, and where to look."""
    started, downloaded = time.monotonic(), 0
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
            with open(destination, "wb") as target:
                while chunk := response.read(CHUNK_SIZE):
                    target.write(chunk)
                    downloaded += len(chunk)
    except urllib.error.HTTPError as error:
        body = error.read(500).decode(errors="replace")
        raise IngestError(
            f"the SEPA proxy at {url} answered {error.code}: {body}. {PROXY_HINT}: "
            "check the proxy's log.") from error
    except (urllib.error.URLError, ConnectionError, TimeoutError,
            http.client.HTTPException) as error:
        # Only the network's errors: a full /tmp is not the proxy's fault.
        reason = getattr(error, "reason", error)
        raise IngestError(
            f"the SEPA proxy at {url} did not answer ({reason}). {PROXY_HINT}: check "
            "that both are up and that SEPA_API_URL is the tunnel's current URL.") from error
    log.info("downloaded %.1f MB in %.0fs", downloaded / 1e6, time.monotonic() - started)
    return destination


def fetch_daily_archive(sepa_api_url: str) -> str:
    if LOCAL_ARCHIVE and os.path.exists(LOCAL_ARCHIVE):
        log.info("using local archive %s", LOCAL_ARCHIVE)
        return LOCAL_ARCHIVE
    return download(archive_url(sepa_api_url))


def chains_in(archive: zipfile.ZipFile) -> list[Chain]:
    """The chains published that day, ordered by chain number."""
    chains: list[Chain] = []
    for entry in archive.infolist():
        if entry.is_dir() or not entry.filename.lower().endswith(".zip"):
            continue

        match = CHAIN_FILENAME.search(Path(entry.filename).name)
        if not match:
            log.warning("skipped, unexpected name: %s", entry.filename)
        elif entry.file_size == 0:
            log.warning("skipped, published empty: %s", entry.filename)
        else:
            chains.append(Chain(entry, match.group(1), match.group(2)))

    # Each chain stamps its own date on its filename, and that is the date the
    # data is for. What the portal said is only how the file got here.
    if published := {chain.date for chain in chains}:
        log.info("the archive holds %s", ", ".join(sorted(published)))

    if TARGET_DATE:
        stale = [chain for chain in chains if chain.date != TARGET_DATE]
        for chain in stale:
            log.warning("skipped, dated %s and %s was asked for: %s",
                        chain.date, TARGET_DATE, chain.entry.filename)
        chains = [chain for chain in chains if chain.date == TARGET_DATE]
        if not chains:
            raise IngestError(
                f"nothing in the archive is dated {TARGET_DATE}")

    chains.sort(key=lambda chain: int(chain.number))
    return chains[:CHAIN_LIMIT] if CHAIN_LIMIT else chains


def is_stored(s3: Any, chain: Chain) -> bool:
    try:
        stored = s3.head_object(Bucket=SETTINGS.bucket, Key=chain.s3_key)
    except ClientError as error:
        # Only a missing object is "not stored": anything else (a denial, a
        # throttle) fails the run rather than uploading again.
        if error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return stored["ContentLength"] == chain.size


def store(s3: Any, archive: zipfile.ZipFile, chain: Chain) -> None:
    content = archive.read(chain.entry)
    s3.put_object(Bucket=SETTINGS.bucket, Key=chain.s3_key, Body=content)

    stored = s3.head_object(Bucket=SETTINGS.bucket, Key=chain.s3_key)["ContentLength"]
    if stored != len(content):
        raise IngestError(
            f"{chain.s3_key}: sent {len(content)} bytes, S3 reports {stored}")


def ingest(s3: Any, archive_path: str) -> IngestSummary:
    """Store each chain's file in raw/, once.

    A file already there with the same size is left alone: a new object (or a
    new version of one) is what makes a job, so running twice the same day
    makes no job twice. Reprocessing is a deliberate choice, not a side effect
    of running twice: REPROCESS=1 stores every file again.
    """
    summary = IngestSummary()

    with zipfile.ZipFile(archive_path) as archive:
        chains = chains_in(archive)
        if not chains:
            raise IngestError("the SEPA archive contains no usable chain")
        log.info("%d chains in the SEPA archive", len(chains))
        summary.chains = len(chains)
        summary.feed_dates = sorted({chain.date for chain in chains})

        for chain in chains:
            if not REPROCESS and is_stored(s3, chain):
                summary.skipped += 1
                outcome = "present"
            else:
                store(s3, archive, chain)
                summary.stored += 1
                outcome = "stored"
            log.info("chain %3s  %7.1f MB  %s", chain.number, chain.size / 1e6, outcome)

    log.info("done. objects stored=%d skipped=%d", summary.stored, summary.skipped)
    return summary


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """The Lambda's entry point.

    The cron sends no URL and the function uses its SEPA_API_URL; run-ingest
    sends the secret's, when it has one, so a new tunnel needs no new Deploy.
    run-ingest invokes asynchronously (a run outlasts what a connection from
    a CI runner stays open idle) and reads the result from the log: the one
    line that names its `run_id`, `finished: <summary>` or `failed: <why>`.
    """
    configure_logging()
    event = event or {}
    run_id = event.get("run_id", "cron")
    sepa_api_url = event.get("sepa_api_url") or SEPA_API_URL
    try:
        summary = asdict(ingest(aws_client("s3"), fetch_daily_archive(sepa_api_url)))
    except Exception as error:
        # One line, whatever the error carries: it is what run-ingest shows.
        reason = " ".join(f"{type(error).__name__}: {error}".split())
        log.error("ingestion %s failed: %s", run_id, reason)
        raise
    log.info("ingestion %s finished: %s", run_id, json.dumps(summary))
    return summary


if __name__ == "__main__":
    print(json.dumps(handler({}, None)))
