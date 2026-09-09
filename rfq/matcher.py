"""Drives resolve() + quantity.py over a whole lines.json, assembling
predictions.json line items. Must accept ANY conforming lines.json --
never assumes anything about how it was produced.
"""

from __future__ import annotations

import dataclasses

from rfq.catalog import Catalog
from rfq.parse import Query, parse_line
from rfq.quantity import find_base_unit_sibling, resolve_quantity
from rfq.resolve import Resolution, resolve


def _build_item(query: Query, resolution: Resolution, catalog: Catalog) -> dict:
    item: dict = {"raw": query.raw, "why": resolution.why}

    if resolution.sku is not None:
        sku_row = catalog.by_sku[resolution.sku.upper()]

        # "A unit word is evidence; its absence when both fit is ambiguity"
        # (README) -- not exercised by name in the dev set, but a base-unit
        # sibling existing for a pack sku, with no unit word stated, means
        # the attribute filter alone can't tell which one the customer
        # meant. Caught here (post-resolve), since it's a quantity-
        # availability question, not an attribute-filtering one.
        if query.unit_word is None and not query.qty_is_vague and sku_row.pack_qty > 1:
            base_sibling = find_base_unit_sibling(catalog, sku_row)
            if base_sibling is not None:
                item["qty"] = query.qty_stated
                item["sku"] = None
                item["abstain"] = "ambiguous"
                item["candidates"] = sorted([sku_row.sku, base_sibling.sku])
                item["why"] = (
                    "a unit word is evidence; its absence when both a base "
                    "and a pack sku fit is ambiguity"
                )
                return item

        qty, uom = resolve_quantity(query, sku_row)
        item["qty"] = qty
        item["uom"] = uom
        item["sku"] = resolution.sku
    else:
        item["qty"] = None if query.qty_is_vague else query.qty_stated
        item["sku"] = None
        item["abstain"] = resolution.abstain
        if resolution.candidates:
            item["candidates"] = resolution.candidates

    return item


def match_line(raw: str, catalog: Catalog, sender_domain: str | None = None) -> dict:
    query = parse_line(raw)
    resolution = resolve(query, catalog, sender_domain)
    return _build_item(query, resolution, catalog)


def match_lines(
    lines_by_id: dict[str, list[str]],
    catalog: Catalog,
    sender_domains: dict[str, str | None] | None = None,
    supersedes: dict[str, str | None] | None = None,
) -> dict:
    """lines_by_id may be ANY conforming lines.json -- no assumption about
    provenance. sender_domains/supersedes are optional, best-effort context
    (only available via `run`, never via a cold `match` call on a bare
    lines.json -- see DECISIONS.md for the disclosed limitation this implies
    for customer:<domain> xref lookups when matching in isolation).
    """
    sender_domains = sender_domains or {}
    supersedes = supersedes or {}
    result = {}
    for eid, raws in lines_by_id.items():
        domain = sender_domains.get(eid)
        line_items = []
        last_family: str | None = None
        last_material: str | None = None

        for raw in raws:
            query = parse_line(raw)
            resolution = resolve(query, catalog, domain)
            used_query = query

            # Elliptical continuation within the SAME email (E003-2: "and
            # 20 of the 3/4″" has no family/material of its own and can't
            # resolve on it -- extract.py kept the leading "and" precisely
            # to signal it's anaphoric to the line before it). Only a
            # retry, never the first attempt: a line with its own stated
            # family already resolved (or correctly abstained) on its own
            # merits and must not be second-guessed.
            if resolution.sku is None and query.family is None and last_family is not None:
                retry_query = dataclasses.replace(
                    query,
                    family=last_family,
                    material=query.material or last_material,
                )
                retry_resolution = resolve(retry_query, catalog, domain)
                if retry_resolution.sku is not None:
                    used_query = retry_query
                    resolution = dataclasses.replace(
                        retry_resolution,
                        why=retry_resolution.why
                        + "; family/material inherited from the previous line in this email",
                    )

            item = _build_item(used_query, resolution, catalog)
            line_items.append(item)
            if item.get("sku") is not None:
                last_family = used_query.family
                last_material = used_query.material

        result[eid] = {"supersedes": supersedes.get(eid), "line_items": line_items}
    return result
