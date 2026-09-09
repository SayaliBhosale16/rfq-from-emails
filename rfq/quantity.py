"""qty + stated unit -> qty in the matched sku's uom.

- Last step: the count is converted into the matched SKU's unit — eaches, boxes, feet, reels — or set to null if it doesn't divide cleanly into a pack.

Quantity resolution is independent of sku resolution (E006-2: qty is null
but the sku still resolves) -- this module never blocks or overrides a
resolve() decision, it only decides the number.
"""

from __future__ import annotations

from rfq import normalize
from rfq.catalog import Catalog, CatalogRow
from rfq.parse import Query

M_TO_FT = 3.2808


def find_base_unit_sibling(catalog: Catalog, row: CatalogRow) -> CatalogRow | None:
    """Is there an active sibling row -- same family/size/material/thread,
    pack_qty==1 -- for this pack sku? Used to detect the "both a base-unit
    and pack sku exist, no unit word stated" ambiguity (README: "a unit
    word is evidence; its absence when both fit is ambiguity").
    """
    for candidate in catalog.by_family.get(row.family, []):
        if candidate.sku == row.sku or candidate.status != "active":
            continue
        if (
            candidate.pack_qty == 1
            and candidate.size_key == row.size_key
            and candidate.material_key == row.material_key
            and (candidate.thread or "") == (row.thread or "")
        ):
            return candidate
    return None


def resolve_quantity(query: Query, sku_row: CatalogRow) -> tuple[float | None, str]:
    """Returns (qty_in_sku_uom, uom_code)."""
    if query.qty_is_vague or query.qty_stated is None:
        return None, sku_row.uom

    unit = query.unit_word

    if unit is not None and unit != normalize.VAGUE_UNIT:
        if unit == sku_row.uom:
            # Stated unit word matches the matched row's own uom directly --
            # evidence, not a count to convert (E006-3: "12 pairs" -> PR row,
            # E017-2: "2 reels" -> RL row, no math applied either way).
            return query.qty_stated, sku_row.uom
        if unit == "M" and sku_row.uom == "FT":
            return round(query.qty_stated * M_TO_FT, 1), "FT"
        # Unit word stated but doesn't match this row's uom and isn't a
        # known conversion (m->ft) -- unresolvable, not a guess.
        return None, sku_row.uom

    # No unit word stated -- customer means the base unit.
    if sku_row.pack_qty == 1:
        return query.qty_stated, sku_row.uom

    # Pack-only sku, bare number, no unit word: pack-divide unconditionally
    # (E014-7: 400 flat washers -> qty 4, uom BX -- no base-unit washer sku
    # exists at all). Never round when it doesn't divide evenly -- that's a
    # question for the customer (README: "quantities in this inbox always
    # divide into whole packs -- unless they don't").
    if query.qty_stated % sku_row.pack_qty == 0:
        return query.qty_stated / sku_row.pack_qty, sku_row.uom
    return None, sku_row.uom
