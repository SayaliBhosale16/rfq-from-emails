#!/usr/bin/env python3
"""Debugging tool: one raw line in -> full parse + filter trace out.

    python tools/explain.py "20 x 1/2 90 elbows" --domain cobaltskids.com

Walks resolve()'s steps manually (not as a black box) so a wrong match can
be diagnosed by eye: identifier-lookup attempts, family candidate count
before filtering, each attribute filter applied and the surviving count
after, final decision + why, then the resolve_quantity() branch taken.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rfq import normalize
from rfq.catalog import family_candidates, load, lookup_identifier
from rfq.parse import parse_line
from rfq.quantity import find_base_unit_sibling, resolve_quantity
from rfq.resolve import resolve

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def explain(raw: str, domain: str | None) -> None:
    catalog = load(DATA_DIR / "catalog.csv", DATA_DIR / "xref.csv")
    query = parse_line(raw)

    print(f"raw:      {raw!r}")
    print(f"domain:   {domain!r}")
    print()
    print("parsed query:")
    for field_name in (
        "qty_stated", "qty_is_vague", "unit_word", "size", "length",
        "material", "thread", "pole", "family", "identifier_tokens",
        "named_options", "stray_numbers",
    ):
        print(f"  {field_name:16s} = {getattr(query, field_name)!r}")
    print()

    print("identifier lookup attempts:")
    seen = set()
    hit = None
    for tok in [query.raw, *query.identifier_tokens]:
        if tok in seen:
            continue
        seen.add(tok)
        row = lookup_identifier(catalog, tok, domain)
        status = f"HIT -> {row.sku} (status={row.status})" if row else "miss"
        print(f"  {tok!r:40s} {status}")
        if row and hit is None:
            hit = row
    print()

    if hit is None:
        print("attribute filter trace:")
        if query.family:
            candidates = family_candidates(catalog, query.family)
            print(f"  family={query.family!r}: {len(candidates)} rows")
        else:
            candidates = [r for r in catalog.rows if r.status == "active"]
            print(f"  no family resolved: scanning whole catalog ({len(candidates)} active rows)")

        if query.named_options:
            candidates = [
                r for r in candidates
                if any(r.size_key == opt.get("size") for opt in query.named_options)
            ]
            print(f"  named_options={query.named_options}: {len(candidates)} rows")
        else:
            if query.size is not None:
                candidates = [r for r in candidates if r.size_key == query.size]
                print(f"  size={query.size!r}: {len(candidates)} rows")
            if query.material is not None:
                candidates = [r for r in candidates if r.material_key == query.material]
                print(f"  material={query.material!r}: {len(candidates)} rows")
            if query.thread is not None:
                candidates = [
                    r for r in candidates if (r.thread or "").upper() == str(query.thread).upper()
                ]
                print(f"  thread={query.thread!r}: {len(candidates)} rows")
            if query.length is not None:
                candidates = [
                    r for r in candidates if normalize.fold_size(r.length) == query.length
                ]
                print(f"  length={query.length!r}: {len(candidates)} rows")
            if query.pole is not None:
                candidates = [r for r in candidates if r.pole == query.pole]
                print(f"  pole={query.pole!r}: {len(candidates)} rows")
        print(f"  surviving skus: {sorted(r.sku for r in candidates if r.status == 'active')}")
        print()

    resolution = resolve(query, catalog, domain)
    print("resolution:")
    print(f"  sku:        {resolution.sku}")
    print(f"  abstain:    {resolution.abstain}")
    print(f"  candidates: {resolution.candidates}")
    print(f"  why:        {resolution.why}")
    print()

    if resolution.sku is not None:
        sku_row = catalog.by_sku[resolution.sku.upper()]
        base_sibling = find_base_unit_sibling(catalog, sku_row)
        print("quantity resolution:")
        print(f"  matched row uom={sku_row.uom!r} pack_qty={sku_row.pack_qty}")
        print(f"  base-unit sibling: {base_sibling.sku if base_sibling else None}")
        qty, uom = resolve_quantity(query, sku_row)
        print(f"  qty={qty!r}  uom={uom!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", help="the raw line string to explain")
    ap.add_argument("--domain", default=None, help="sender domain context (optional)")
    args = ap.parse_args(argv)
    explain(args.raw, args.domain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
