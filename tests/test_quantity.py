from pathlib import Path

import pytest

from rfq import catalog as catmod
from rfq.parse import parse_line
from rfq.quantity import resolve_quantity

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def catalog():
    return catmod.load(DATA / "catalog.csv", DATA / "xref.csv")


def _qty(raw, sku, catalog):
    query = parse_line(raw)
    row = catalog.by_sku[sku.upper()]
    return resolve_quantity(query, row)


def test_E006_1_meters_converts_to_feet(catalog):
    qty, uom = _qty("30 meters of 3/8 push-lok hose", "HOSE-3/8-PUSH", catalog)
    assert uom == "FT"
    assert qty == pytest.approx(98.4, rel=0.05)


def test_E017_2_reels_no_conversion(catalog):
    qty, uom = _qty("3/8 push lock hose                      2 reels", "HOSE-3/8-PUSH-R50", catalog)
    assert qty == 2
    assert uom == "RL"


def test_E006_3_pairs_no_conversion(catalog):
    qty, uom = _qty("12 pairs of large nitrile gloves", "GLV-NIT-L", catalog)
    assert qty == 12
    assert uom == "PR"


def test_E014_7_pack_only_base_shaped_text_still_converts(catalog):
    qty, uom = _qty("400 – 3/8 flat washers zinc", "FW-3/8-ZP", catalog)
    assert qty == 4
    assert uom == "BX"


def test_vague_quantifier_qty_is_null(catalog):
    qty, uom = _qty("a couple of the 1-inch brass ball valves", "BV-1-BR-FNPT", catalog)
    assert qty is None
    assert uom == "EA"


def test_non_dividing_pack_qty_returns_null_never_rounds(catalog):
    # FW-3/8-ZP is boxed 100/box (per catalog); 250 doesn't divide evenly.
    query = parse_line("250 – 3/8 flat washers zinc")
    row = catalog.by_sku["FW-3/8-ZP"]
    qty, uom = resolve_quantity(query, row)
    assert qty is None
    assert uom == row.uom


def test_base_unit_sku_passthrough_no_conversion_needed(catalog):
    qty, uom = _qty("50 pcs 3/8-16 x 1 hex cap screws zinc", "HHCS-3/8-16x1-ZP", catalog)
    assert qty == 50
    assert uom == "EA"
