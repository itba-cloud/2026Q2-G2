"""Reading and validating a SEPA chain archive.

Pure parsing: this module touches no network, no database and no S3, so the
rules that decide what is a usable row can be read — and tested — on their own.

A chain archive holds three pipe-separated files:

    comercio.csv    the company and its banners (Coto, Disco, Vea…)
    sucursales.csv  its branches
    productos.csv   a price per product and branch, the bulk of the file
"""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from typing import Iterator

DELIMITER = "|"
# The files are exported with a byte-order mark that would otherwise end up
# glued to the first column name.
ENCODING = "utf-8-sig"

COMPANY_FILE = "comercio.csv"
BRANCH_FILE = "sucursales.csv"
PRICE_FILE = "productos.csv"

REQUIRED_BRANCH_COLUMNS = frozenset({
    "id_comercio", "id_bandera", "id_sucursal",
    "sucursales_nombre", "sucursales_provincia", "sucursales_localidad",
})
REQUIRED_PRICE_COLUMNS = frozenset({
    "id_comercio", "id_bandera", "id_sucursal", "id_producto",
    "productos_descripcion", "productos_marca", "productos_precio_lista",
})

EAN_LENGTH = 13
# Above this a price is a data-entry error, not an expensive product.
MAX_PRICE = 10_000_000.0


class SepaFormatError(Exception):
    """The archive will not open, or its header is not the format we know."""


@dataclass(frozen=True)
class Branch:
    company_id: int
    banner_id: int
    branch_id: str
    banner_name: str | None
    legal_name: str | None
    name: str
    kind: str | None
    province: str
    locality: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Product:
    ean: str
    description: str
    brand: str
    presentation: str | None


@dataclass(frozen=True)
class Price:
    feed_date: str
    ean: str
    company_id: int
    banner_id: int
    branch_id: str
    amount: float


@dataclass(frozen=True)
class Rejected:
    line_number: int
    reason: str
    content: str


@dataclass
class Contents:
    """Everything one chain archive yielded, valid and invalid."""

    branches: list[Branch] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    prices: list[Price] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)

    @property
    def rejection_ratio(self) -> float:
        """Share of price rows thrown away. A high one means the chain changed
        its format, not that it published bad data."""
        considered = len(self.prices) + len(self.rejected)
        return len(self.rejected) / considered if considered else 0.0


def _rows(archive: zipfile.ZipFile, filename: str,
          required: frozenset[str]) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (line number, row keyed by column name).

    Reading by column name rather than by position is what lets a chain add
    columns without breaking us, and lets us notice when one goes missing.
    """
    try:
        raw = archive.open(filename)
    except KeyError as error:
        raise SepaFormatError(f"the archive has no {filename}") from error

    with io.TextIOWrapper(raw, encoding=ENCODING, errors="replace") as text:
        reader = csv.DictReader(text, delimiter=DELIMITER)
        present = {name.strip().lower() for name in (reader.fieldnames or [])}
        if missing := required - present:
            raise SepaFormatError(
                f"{filename} is missing columns: {', '.join(sorted(missing))}")

        # Line 1 is the header, so the first data row is line 2.
        for line_number, row in enumerate(reader, start=2):
            yield line_number, row


def _text(row: dict[str, str], column: str, limit: int | None = None) -> str:
    value = (row.get(column) or "").strip()
    return value[:limit] if limit else value


def _number(row: dict[str, str], column: str) -> float | None:
    try:
        return float(_text(row, column))
    except ValueError:
        return None


def read_banners(archive: zipfile.ZipFile) -> dict[tuple[int, int], tuple[str, str]]:
    """Banner name and legal name, keyed by (company, banner).

    One company runs several banners: Cencosud publishes Disco, Jumbo and Vea
    from a single archive, and a shopper cares which one they are standing in.
    """
    banners: dict[tuple[int, int], tuple[str, str]] = {}
    for _, row in _rows(archive, COMPANY_FILE, frozenset({"id_comercio", "id_bandera"})):
        try:
            key = (int(_text(row, "id_comercio")), int(_text(row, "id_bandera")))
        except ValueError:
            continue
        banners[key] = (_text(row, "comercio_bandera_nombre"),
                        _text(row, "comercio_razon_social"))
    return banners


def read_branches(archive: zipfile.ZipFile,
                  banners: dict[tuple[int, int], tuple[str, str]]) -> list[Branch]:
    branches = []
    for _, row in _rows(archive, BRANCH_FILE, REQUIRED_BRANCH_COLUMNS):
        try:
            company_id = int(_text(row, "id_comercio"))
            banner_id = int(_text(row, "id_bandera"))
        except ValueError:
            continue

        banner_name, legal_name = banners.get((company_id, banner_id), ("", ""))
        branches.append(Branch(
            company_id=company_id,
            banner_id=banner_id,
            branch_id=_text(row, "id_sucursal"),
            banner_name=banner_name or None,
            legal_name=legal_name or None,
            name=_text(row, "sucursales_nombre"),
            kind=_text(row, "sucursales_tipo") or None,
            province=_text(row, "sucursales_provincia"),
            locality=_text(row, "sucursales_localidad"),
            latitude=_number(row, "sucursales_latitud"),
            longitude=_number(row, "sucursales_longitud"),
        ))
    return branches


BranchKey = tuple[int, int, str]


def _rejection_reason(row: dict[str, str], branches: set[BranchKey]) -> str | None:
    """Why this price row cannot be used, or None if it can."""
    ean = _text(row, "id_producto")
    if not ean.isdigit() or len(ean) != EAN_LENGTH:
        return "barcode is not 13 digits"
    if int(ean) == 0:
        return "barcode is all zeros"

    amount = _number(row, "productos_precio_lista")
    if amount is None:
        return "price is not a number"
    if amount <= 0:
        return "price is zero or negative"
    if amount > MAX_PRICE:
        return "price is out of range"

    try:
        key = (int(_text(row, "id_comercio")), int(_text(row, "id_bandera")),
               _text(row, "id_sucursal"))
    except ValueError:
        return "company or banner is not a number"

    # Chains do publish prices for branches their own sucursales.csv never
    # lists. A price nobody can locate is not comparable to anything, so it is
    # dropped here rather than loaded as an orphan.
    if key not in branches:
        return "branch is not listed in the archive"
    return None


def branch_keys(branches: list[Branch]) -> set[BranchKey]:
    return {(b.company_id, b.banner_id, b.branch_id) for b in branches}


def read_prices(archive: zipfile.ZipFile, feed_date: str, branches: set[BranchKey],
                limit: int = 0) -> tuple[list[Price], list[Product], list[Rejected]]:
    """Every usable price row, the products they mention, and what was dropped."""
    prices: list[Price] = []
    products: dict[str, Product] = {}
    rejected: list[Rejected] = []

    for line_number, row in _rows(archive, PRICE_FILE, REQUIRED_PRICE_COLUMNS):
        if limit and len(prices) >= limit:
            break

        if reason := _rejection_reason(row, branches):
            original = DELIMITER.join(str(v) for v in row.values() if v is not None)
            rejected.append(Rejected(line_number, reason, original))
            continue

        ean = _text(row, "id_producto")
        prices.append(Price(
            feed_date=feed_date,
            ean=ean,
            company_id=int(_text(row, "id_comercio")),
            banner_id=int(_text(row, "id_bandera")),
            branch_id=_text(row, "id_sucursal"),
            amount=round(float(_text(row, "productos_precio_lista")), 2),
        ))

        if ean not in products:
            quantity = _text(row, "productos_cantidad_presentacion")
            unit = _text(row, "productos_unidad_medida_presentacion")
            products[ean] = Product(
                ean=ean,
                description=_text(row, "productos_descripcion", limit=200),
                brand=_text(row, "productos_marca", limit=80),
                presentation=f"{quantity} {unit}".strip() or None,
            )

    return prices, list(products.values()), rejected


def read(archive_path: str, feed_date: str, limit: int = 0) -> Contents:
    """Read one chain archive end to end."""
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as error:
        raise SepaFormatError(f"the archive will not open: {error}") from error

    with archive:
        banners = read_banners(archive)
        branches = read_branches(archive, banners)
        prices, products, rejected = read_prices(
            archive, feed_date, branch_keys(branches), limit)

    return Contents(branches=branches, products=products,
                    prices=prices, rejected=rejected)
