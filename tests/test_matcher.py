import json
from pathlib import Path

import pytest

from rfq import catalog as catmod
from rfq import matcher

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def catalog():
    return catmod.load(DATA / "catalog.csv", DATA / "xref.csv")


@pytest.fixture(scope="module")
def answer_key():
    return json.loads((DATA / "dev" / "answer_key.json").read_text())


def test_match_line_e001_full_shape(catalog):
    item = matcher.match_line("50 pcs 3/8-16 x 1 hex cap screws zinc", catalog)
    assert item["raw"] == "50 pcs 3/8-16 x 1 hex cap screws zinc"
    assert item["sku"] == "HHCS-3/8-16x1-ZP"
    assert item["qty"] == 50
    assert item["uom"] == "EA"
    assert "abstain" not in item
    assert item["why"]


def test_match_line_abstain_requires_candidates(catalog):
    item = matcher.match_line("20 × ½ 90 elbows", catalog)
    assert item["sku"] is None
    assert item["abstain"] == "ambiguous"
    assert len(item["candidates"]) >= 2


def test_match_lines_elliptical_inheritance_within_email(catalog):
    lines_by_id = {
        "E003": [
            "50 of the 1/2″ black iron 90s",
            "and 20 of the 3/4″",
        ]
    }
    result = matcher.match_lines(lines_by_id, catalog)
    items = result["E003"]["line_items"]
    assert items[0]["sku"] == "EL90-1/2-BI"
    assert items[1]["sku"] == "EL90-3/4-BI"
    assert "inherited" in items[1]["why"]


def test_match_lines_no_inheritance_across_different_emails(catalog):
    # A bare "and 20 of the 3/4″" with NO prior line in ITS OWN email must
    # not inherit from some other email's context.
    lines_by_id = {"E999": ["and 20 of the 3/4″"]}
    result = matcher.match_lines(lines_by_id, catalog)
    item = result["E999"]["line_items"][0]
    assert item["sku"] is None
    assert item["abstain"] == "ambiguous"


def test_full_dev_set_sku_abstain_accuracy(catalog, answer_key):
    """82/86 lines resolve correctly in cold match (no sender domain) --
    the remaining 4 are documented, structurally-unresolvable limitations:
    TSM64072 needs customer-domain xref (E003-4/E010-4, no domain in a
    bare lines.json), and E010-1/E010-2's correction clause needs E003's
    context across a supersedes link lines.json can't carry.
    """
    known_gaps = {"E003-4", "E010-4", "E010-1", "E010-2"}
    lines_by_id = {
        eid: [li["raw"] for li in edata["line_items"]]
        for eid, edata in answer_key["emails"].items()
    }
    predictions = matcher.match_lines(lines_by_id, catalog)

    total = correct = 0
    for eid, edata in answer_key["emails"].items():
        for li, pred in zip(edata["line_items"], predictions[eid]["line_items"]):
            total += 1
            if pred.get("sku") == li["sku"] and pred.get("abstain") == li["abstain"]:
                correct += 1
            else:
                assert li["id"] in known_gaps, f"new regression at {li['id']}: {pred}"
    assert correct / total >= 0.95
