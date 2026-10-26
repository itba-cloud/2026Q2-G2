"""Reading a merchant's own price list.

Not every comercio LGDC compares is required to report to SEPA. One that
isn't gets to upload its own prices instead of being ingested from the daily
archive. The file is a plain CSV, not a SEPA zip, so it needs its own reader
-- but it returns the exact same `sepa.Contents` shape `sepa.read()` does,
which is what lets the rest of the worker (store_rejects, store_curated,
load) run unmodified against either source.

Expected columns, comma-separated, with a header row:

    ean,price[,description,brand,presentation]

The branch itself is not derived from the file. It is created once, at
registration, by the backend, with the coordinates the merchant supplies --
that is what puts the merchant on the geo map. Reprocessing an upload only
ever touches its prices.
"""
from __future__ import annotations

import csv
from typing import Any

import sepa

DELIMITER = ","
ENCODING = "utf-8-sig"
REQUIRED_COLUMNS = frozenset({"ean", "price"})


def _text(row: dict[str, str], column: str, limit: int | None = None) -> str:
    value = (row.get(column) or "").strip()
    return value[:limit] if limit else value


def _number(row: dict[str, str], column: str) -> float | None:
    try:
        return float(_text(row, column))
    except ValueError:
        return None


def _rejection_reason(row: dict[str, str]) -> str | None:
    """Same rules sepa.py applies to a SEPA row: a merchant's typo is exactly
    as unusable as a chain's, and REJECT_THRESHOLD should catch it the same
    way it catches a chain that changed its format."""
    ean = _text(row, "ean")
    if not ean.isdigit() or len(ean) != sepa.EAN_LENGTH:
        return "barcode is not 13 digits"
    if int(ean) == 0:
        return "barcode is all zeros"

    amount = _number(row, "price")
    if amount is None:
        return "price is not a number"
    if amount <= 0:
        return "price is zero or negative"
    if amount > sepa.MAX_PRICE:
        return "price is out of range"
    return None


def read(csv_path: str, feed_date: str, company_id: int, banner_id: int,
         branch_id: str, limit: int = 0) -> sepa.Contents:
    """Read one merchant's price list end to end."""
    try:
        handle = open(csv_path, encoding=ENCODING, newline="")
    except OSError as error:
        raise sepa.SepaFormatError(f"the file will not open: {error}") from error

    # A file that is not UTF-8 text (a ZIP, a spreadsheet) or not CSV fails
    # while it is read: it is as broken as a missing column, not transient.
    try:
        with handle as text:
            return _read_rows(text, feed_date, company_id, banner_id, branch_id, limit)
    except (UnicodeDecodeError, csv.Error) as error:
        raise sepa.SepaFormatError(f"the file is not a UTF-8 CSV: {error}") from error


def _read_rows(text: Any, feed_date: str, company_id: int, banner_id: int,
               branch_id: str, limit: int) -> sepa.Contents:
    """The rows of an open price list, in the `sepa.Contents` shape."""
    reader = csv.DictReader(text, delimiter=DELIMITER)
    present = {(name or "").strip().lower() for name in (reader.fieldnames or [])}
    if missing := REQUIRED_COLUMNS - present:
        raise sepa.SepaFormatError(f"missing columns: {', '.join(sorted(missing))}")

    prices: list[sepa.Price] = []
    products: dict[str, sepa.Product] = {}
    rejected: list[sepa.Rejected] = []

    # Line 1 is the header, so the first data row is line 2.
    for line_number, raw_row in enumerate(reader, start=2):
        if limit and len(prices) >= limit:
            break
        row = {(key or "").strip().lower(): value for key, value in raw_row.items()}

        if reason := _rejection_reason(row):
            original = DELIMITER.join(str(v) for v in row.values() if v is not None)
            rejected.append(sepa.Rejected(line_number, reason, original))
            continue

        ean = _text(row, "ean")
        prices.append(sepa.Price(
            feed_date=feed_date, ean=ean, company_id=company_id,
            banner_id=banner_id, branch_id=branch_id,
            amount=round(float(_text(row, "price")), 2)))

        if ean not in products:
            products[ean] = sepa.Product(
                ean=ean, description=_text(row, "description", limit=200),
                brand=_text(row, "brand", limit=80),
                presentation=_text(row, "presentation") or None)

    if not prices and not rejected:
        raise sepa.SepaFormatError("the file has no data rows")

    return sepa.Contents(branches=[], products=list(products.values()),
                         prices=prices, rejected=rejected)
