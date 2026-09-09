from pathlib import Path

import pytest

from rfq import catalog as catmod
from rfq.parse import parse_line
from rfq.resolve import resolve

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def catalog():
    return catmod.load(DATA / "catalog.csv", DATA / "xref.csv")


def _resolve(raw, catalog, domain=None):
    return resolve(parse_line(raw), catalog, domain)


def test_E002_1_vs_E014_12_candidate_count_tracks_catalog(catalog):
    r1 = _resolve("20 × ½ 90 elbows", catalog)
    assert r1.abstain == "ambiguous"
    assert sorted(r1.candidates) == ["EL90-1/2-BI", "EL90-1/2-BR", "EL90-1/2-GV", "EL90-1/2-SS"]

    r2 = _resolve("30 – 3/4 90 elbow", catalog)
    assert r2.abstain == "ambiguous"
    assert sorted(r2.candidates) == ["EL90-3/4-BI", "EL90-3/4-BR", "EL90-3/4-SS"]


def test_E009_4_vs_E002_1_different_code_paths(catalog):
    discontinued = _resolve("EL90-1/2-GALV | 1/2 galv 90 | 30", catalog, "acme-fab.com")
    assert discontinued.sku is None
    assert discontinued.abstain == "discontinued"
    assert discontinued.candidates == ["EL90-1/2-GV"]

    ambiguous = _resolve("20 × ½ 90 elbows", catalog)
    assert ambiguous.abstain == "ambiguous"
    assert "EL90-1/2-GALV" not in ambiguous.candidates


def test_E009_2_near_miss_identifier_falls_through_to_attributes(catalog):
    r = _resolve(
        "HCS-1/2-13X2-1/2-SS | hex cap screw 1/2-13 x 2-1/2 stainless | 150",
        catalog,
        "acme-fab.com",
    )
    assert r.sku == "HHCS-1/2-13x2-1/2-SS"
    assert r.abstain is None


def test_E006_1_vs_E017_2_same_family_different_sku_by_unit_word(catalog):
    meters = _resolve("30 meters of 3/8 push-lok hose", catalog)
    assert meters.sku == "HOSE-3/8-PUSH"

    reels = _resolve("3/8 push lock hose                      2 reels", catalog)
    assert reels.sku == "HOSE-3/8-PUSH-R50"


def test_E014_7_pack_only_base_shaped_text_still_resolves_sku(catalog):
    r = _resolve("400 – 3/8 flat washers zinc", catalog)
    assert r.sku == "FW-3/8-ZP"


def test_E002_4_named_two_options_not_whole_family(catalog):
    r = _resolve("8 – brass ball valves, 1/2 or 3/4 threaded, whichever is in stock", catalog)
    assert r.abstain == "ambiguous"
    assert sorted(r.candidates) == ["BV-1/2-BR-FNPT", "BV-3/4-BR-FNPT"]


def test_no_pvc_ball_valve_not_in_catalog(catalog):
    r = _resolve("4 – 3/4 PVC ball valve", catalog)
    assert r.sku is None
    assert r.abstain == "not_in_catalog"
    assert "pvc" in r.why.lower()


def test_vague_quantifier_sku_still_resolves(catalog):
    r = _resolve("a couple of the 1-inch brass ball valves", catalog)
    assert r.sku == "BV-1-BR-FNPT"


def test_identifier_lookup_before_attribute_filter(catalog):
    # A direct sku typed in-line must resolve via identifier lookup, never
    # via description-based attribute filtering, even when it happens to
    # also be attribute-describable.
    r = _resolve("EL90-1/2-BR", catalog)
    assert r.sku == "EL90-1/2-BR"
    assert r.why == "identifier/xref exact match"


def test_customer_xref_scoped_to_domain(catalog):
    wrong_domain = _resolve("AF-04202 | 1/2-13 x 2 HHCS SS | 200", catalog, "someoneelse.com")
    # Falls through to attribute filtering, not blocked -- and still
    # resolves correctly via the redundant description text in that row.
    assert wrong_domain.sku == "HHCS-1/2-13x2-SS"
    assert wrong_domain.why != "identifier/xref exact match"

    right_domain = _resolve("AF-04202 | 1/2-13 x 2 HHCS SS | 200", catalog, "acme-fab.com")
    assert right_domain.sku == "HHCS-1/2-13x2-SS"
    assert right_domain.why == "identifier/xref exact match"
