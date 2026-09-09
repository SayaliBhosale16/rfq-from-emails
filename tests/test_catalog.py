from pathlib import Path

import pytest

from rfq import catalog as catmod

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def catalog():
    return catmod.load(DATA / "catalog.csv", DATA / "xref.csv")


def test_row_count_matches_file(catalog):
    with open(DATA / "catalog.csv", encoding="utf-8") as f:
        expected = sum(1 for _ in f) - 1  # minus header
    assert len(catalog.rows) == expected


def test_discontinued_rows_all_have_replaced_by(catalog):
    for row in catalog.rows:
        if row.status == "discontinued":
            assert row.replaced_by, f"{row.sku} is discontinued with no replaced_by"
        else:
            assert not row.replaced_by, f"{row.sku} is active but has replaced_by"


def test_el90_candidate_counts_track_catalog_exactly(catalog):
    half = sorted(
        r.sku
        for r in catalog.by_family["EL90"]
        if r.size_key == "1/2" and r.status == "active"
    )
    three_quarter = sorted(
        r.sku
        for r in catalog.by_family["EL90"]
        if r.size_key == "3/4" and r.status == "active"
    )
    assert half == ["EL90-1/2-BI", "EL90-1/2-BR", "EL90-1/2-GV", "EL90-1/2-SS"]
    assert three_quarter == ["EL90-3/4-BI", "EL90-3/4-BR", "EL90-3/4-SS"]


def test_no_pvc_material_anywhere(catalog):
    assert not any(r.material_key == "PVC" for r in catalog.rows)


def test_lookup_identifier_direct_sku(catalog):
    row = catmod.lookup_identifier(catalog, "EL90-1/2-BR", None)
    assert row is not None and row.sku == "EL90-1/2-BR"


def test_lookup_identifier_customer_xref_domain_scoped(catalog):
    assert catmod.lookup_identifier(catalog, "AF-04202", "acme-fab.com").sku == "HHCS-1/2-13x2-SS"
    assert catmod.lookup_identifier(catalog, "AF-04202", "someoneelse.com") is None
    assert catmod.lookup_identifier(catalog, "AF-04202", None) is None


def test_lookup_identifier_competitor_xref_unconditional(catalog):
    row = catmod.lookup_identifier(catalog, "IP 58899-0336", "anyone.example.com")
    assert row is not None and row.sku == "BV-1/2-BR-FNPT"


def test_lookup_identifier_legacy_xref_points_at_discontinued_row(catalog):
    # RL-89466 (legacy) intentionally points at the DEAD sku, not its replacement.
    row = catmod.lookup_identifier(catalog, "RL-89466", "anyone.example.com")
    assert row is not None
    assert row.sku == "EL90-1/2-GALV"
    assert row.status == "discontinued"
    assert row.replaced_by == "EL90-1/2-GV"


def test_lookup_identifier_near_miss_falls_through(catalog):
    # Customer types HCS-1/2-13X2-1/2-SS; real sku is HHCS-1/2-13x2-1/2-SS
    # (double-H). Neither the sku column nor xref has this exact string, so
    # this must resolve via attribute filtering, not identifier lookup.
    assert catmod.lookup_identifier(catalog, "HCS-1/2-13X2-1/2-SS", "acme-fab.com") is None


def test_family_candidates_excludes_discontinued_by_default(catalog):
    rows = catmod.family_candidates(catalog, "EL90")
    assert all(r.status == "active" for r in rows)


def test_family_candidates_unknown_family_returns_empty(catalog):
    assert catmod.family_candidates(catalog, "NOPE") == []
