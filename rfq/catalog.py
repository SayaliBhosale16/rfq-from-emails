"""Load catalog.csv + xref.csv into an attribute-grid index.

Family bucketing and domain-scoped xref lookup live here. resolve.py drives
the actual constraint-filter matching; this module only indexes and looks up.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from rfq import normalize


@dataclass(frozen=True)
class CatalogRow:
    sku: str
    description: str
    category: str
    brand: str
    size: str | None
    material: str | None
    thread: str | None
    length: str | None
    uom: str
    pack_qty: int
    unit_price: float
    status: str  # "active" | "discontinued"
    replaced_by: str | None
    family: str
    size_key: str | None
    material_key: str | None
    pole: str | None


@dataclass(frozen=True)
class XrefEntry:
    alt_number: str
    sku: str
    source: str  # "customer:<domain>" | "competitor:<name>" | "legacy"


@dataclass
class Catalog:
    rows: list[CatalogRow] = field(default_factory=list)
    by_sku: dict[str, CatalogRow] = field(default_factory=dict)
    by_family: dict[str, list[CatalogRow]] = field(default_factory=dict)
    xref: dict[str, list[XrefEntry]] = field(default_factory=dict)


def _family_of(sku: str) -> str:
    """Family is the sku's leading hyphen-delimited token, read positionally --
    NOT a substring search. This matters: "GV" is both the gate-valve family
    prefix (GV-3/4-BR) and the material suffix on galvanized elbows
    (EL90-1/2-GV) -- reading positionally from the front is what keeps those
    from colliding.
    """
    return sku.split("-", 1)[0]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_catalog(path: Path) -> Catalog:
    catalog = Catalog()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            size = _clean(row["size"])
            material = _clean(row["material"])
            description = row["description"]
            # Some families (safety glasses, hard hats, paint markers) never
            # populate the size column at all -- their only distinguishing
            # attribute (color) lives in the description text, so fall back
            # to reading it from there.
            size_key = normalize.fold_size_or_variant(size) if size else normalize.fold_color(description)
            catalog_row = CatalogRow(
                sku=row["sku"].strip(),
                description=description,
                category=row["category"],
                brand=row["brand"],
                size=size,
                material=material,
                thread=_clean(row["thread"]),
                length=_clean(row["length"]),
                uom=row["uom"].strip(),
                pack_qty=int(row["pack_qty"]),
                unit_price=float(row["unit_price"]),
                status=row["status"].strip(),
                replaced_by=_clean(row["replaced_by"]),
                family=_family_of(row["sku"].strip()),
                size_key=size_key,
                material_key=(material.upper() if material else None),
                pole=normalize.fold_pole(description),
            )
            catalog.rows.append(catalog_row)
            catalog.by_sku[catalog_row.sku.upper()] = catalog_row
            catalog.by_family.setdefault(catalog_row.family, []).append(catalog_row)
    return catalog


def load_xref(path: Path, catalog: Catalog) -> None:
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entry = XrefEntry(
                alt_number=row["alt_number"].strip(),
                sku=row["sku"].strip(),
                source=row["source"].strip(),
            )
            key = entry.alt_number.upper()
            catalog.xref.setdefault(key, []).append(entry)


def load(catalog_path: Path, xref_path: Path) -> Catalog:
    catalog = load_catalog(catalog_path)
    load_xref(xref_path, catalog)
    return catalog


def _xref_valid_for_domain(entry: XrefEntry, sender_domain: str | None) -> bool:
    if entry.source.startswith("customer:"):
        domain = entry.source.split(":", 1)[1]
        return sender_domain is not None and sender_domain.lower() == domain.lower()
    # competitor:* and legacy are valid for anyone.
    return True


def lookup_identifier(
    catalog: Catalog, token: str, sender_domain: str | None
) -> CatalogRow | None:
    """Exact-match identifier lookup: our own sku column first, then xref
    (domain-scoped for customer:<domain> entries, unconditional for
    competitor:*/legacy). Returns the row even if discontinued -- resolve.py
    decides what that means, this function just answers "does this identifier
    exist".
    """
    if not token:
        return None
    key = token.strip().upper()
    row = catalog.by_sku.get(key)
    if row is not None:
        return row
    for entry in catalog.xref.get(key, []):
        if _xref_valid_for_domain(entry, sender_domain):
            return catalog.by_sku.get(entry.sku.upper())
    return None


def family_candidates(
    catalog: Catalog, family: str | None, *, include_discontinued: bool = False
) -> list[CatalogRow]:
    if family is None:
        return list(catalog.rows) if include_discontinued else [
            r for r in catalog.rows if r.status == "active"
        ]
    rows = catalog.by_family.get(family, [])
    if include_discontinued:
        return list(rows)
    return [r for r in rows if r.status == "active"]
