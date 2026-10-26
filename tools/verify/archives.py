"""Synthetic SEPA archives, dropped in raw/ the way the ingestion stores them.

Every chain the checks load has an id from 899000001 up, which no real chain
uses, and is dated today (the events suite also loads the day before):
running the checks again replaces the same rows.
What a chain leaves in the data bucket (raw/, curated/, rejected/) is deleted
at the end of the run; its rows in Postgres stay.
"""
from __future__ import annotations

import datetime
import io
import zipfile
from typing import Any, Callable

from harness import Stack, api

FEED_DATE = datetime.date.today().isoformat()

# A job takes a few seconds; a worker starting from zero takes minutes (the
# alarm, the task's start, the image's pull).
JOB_TIMEOUT = 15 * 60


def sepa_archive(chain: int, prices: dict[str, float], rejected_rows: int = 0,
                 latitude: float | None = None, longitude: float | None = None) -> bytes:
    """A chain archive in the SEPA's format: one branch, "1", with these
    prices by barcode, and `rejected_rows` rows with a barcode too short."""
    rows = [f"{chain}|1|1|{ean}|PRODUCTO VERIFY {ean}|LGDC|{price:.2f}"
            for ean, price in prices.items()]
    rows += [f"{chain}|1|1|123|CODIGO CORTO|LGDC|100.00"] * rejected_rows
    where = "|" if latitude is None else f"{latitude}|{longitude}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "comercio.csv",
            "id_comercio|id_bandera|comercio_razon_social|comercio_bandera_nombre\n"
            f"{chain}|1|LGDC Verify SA|LGDC Verify {chain}\n")
        archive.writestr(
            "sucursales.csv",
            "id_comercio|id_bandera|id_sucursal|sucursales_nombre|sucursales_provincia|"
            "sucursales_localidad|sucursales_latitud|sucursales_longitud\n"
            f"{chain}|1|1|Verify {chain}|AR-C|CABA|{where}\n")
        archive.writestr(
            "productos.csv",
            "id_comercio|id_bandera|id_sucursal|id_producto|productos_descripcion"
            "|productos_marca|productos_precio_lista\n" + "\n".join(rows) + "\n")
    return buffer.getvalue()


# The three areas of the data bucket a chain's day leaves files in.
AREAS = ("raw", "curated", "rejected")


def chain_key(area: str, chain: int, filename: str = "", feed_date: str = FEED_DATE) -> str:
    """Where a chain's day lives in an area of the data bucket; without a
    filename, the prefix."""
    return f"{area}/feed_date={feed_date}/chain_id={chain}/{filename}"


def raw_key(chain: int, filename: str, feed_date: str = FEED_DATE) -> str:
    return chain_key("raw", chain, filename, feed_date)


def delete_chain_files_at_end(stack: Stack, chain: int, feed_date: str = FEED_DATE) -> None:
    """Have the clean-up delete everything the chain's day leaves in the bucket."""
    for area in AREAS:
        prefix = chain_key(area, chain, feed_date=feed_date)
        if prefix not in stack.prefixes:
            stack.prefixes.append(prefix)


def put_raw(stack: Stack, chain: int, filename: str, body: bytes,
            feed_date: str = FEED_DATE) -> str:
    """Drop a file in raw/, which S3 announces to the jobs queue. Its key."""
    key = raw_key(chain, filename, feed_date)
    delete_chain_files_at_end(stack, chain, feed_date)
    stack.s3.put_object(Bucket=stack.bucket, Key=key, Body=body)
    return key


def job(stack: Stack, token: str, chain: int,
        feed_date: str = FEED_DATE) -> dict[str, Any] | None:
    """A chain's ingest_job for a day (today by default), as the Pipeline
    screen reads it."""
    jobs = api(stack, "GET", f"/ingest/jobs?feed_date={feed_date}&limit=500", token).json()
    return next((row for row in jobs or [] if row["chain_id"] == str(chain)), None)


def processed_after(stack: Stack, token: str, chain: int, previous: dict[str, Any] | None,
                    status: str, feed_date: str = FEED_DATE
                    ) -> Callable[[], dict[str, Any] | None]:
    """A condition: the chain's job for the day reached `status` after `previous`."""
    def condition() -> dict[str, Any] | None:
        current = job(stack, token, chain, feed_date)
        if current is None or current["status"] != status:
            return None
        if previous is not None and current["updated_at"] == previous["updated_at"]:
            return None
        return current
    return condition
