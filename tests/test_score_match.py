import contextlib
import io

from tools.score_match import score_match

KEY = {
    "emails": {
        "E001": {
            "line_items": [
                {"id": "E001-1", "raw": "a", "sku": "SKU-A", "abstain": None, "qty": 5, "qty_tol": 0},
                {"id": "E001-2", "raw": "b", "sku": None, "abstain": "ambiguous", "qty": 2, "qty_tol": 0},
            ]
        }
    }
}


def _run(pred, key=KEY, detail=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        score_match(pred, key, detail)
    return buf.getvalue()


def test_correct_match_and_correct_abstain():
    pred = {
        "E001": {
            "line_items": [
                {"raw": "a", "sku": "SKU-A", "qty": 5},
                {"raw": "b", "sku": None, "abstain": "ambiguous", "candidates": ["X", "Y"]},
            ]
        }
    }
    out = _run(pred)
    assert "confident wrong matches: 0" in out
    assert "spurious abstentions:    0" in out
    assert "match accuracy:          1.000" in out


def test_confident_wrong_match_counted():
    pred = {
        "E001": {
            "line_items": [
                {"raw": "a", "sku": "SKU-WRONG", "qty": 5},
                {"raw": "b", "sku": None, "abstain": "ambiguous", "candidates": ["X", "Y"]},
            ]
        }
    }
    out = _run(pred)
    assert "confident wrong matches: 1" in out


def test_spurious_abstention_counted():
    pred = {
        "E001": {
            "line_items": [
                {"raw": "a", "sku": None, "abstain": "not_in_catalog", "qty": 5},
                {"raw": "b", "sku": None, "abstain": "ambiguous", "candidates": ["X", "Y"]},
            ]
        }
    }
    out = _run(pred)
    assert "spurious abstentions:    1" in out


def test_uncovered_line_when_raw_not_present():
    pred = {"E001": {"line_items": [{"raw": "a", "sku": "SKU-A", "qty": 5}]}}
    out = _run(pred)
    assert "uncovered key lines:     1" in out


def test_qty_tolerance_respected():
    key = {
        "emails": {
            "E001": {
                "line_items": [
                    {"id": "E001-1", "raw": "a", "sku": "SKU-A", "abstain": None, "qty": 100, "qty_tol": 0.05}
                ]
            }
        }
    }
    pred_within = {"E001": {"line_items": [{"raw": "a", "sku": "SKU-A", "qty": 104}]}}
    pred_outside = {"E001": {"line_items": [{"raw": "a", "sku": "SKU-A", "qty": 110}]}}
    assert "qty accuracy:            1.000" in _run(pred_within, key)
    assert "qty accuracy:            0.000" in _run(pred_outside, key)


def test_null_qty_must_equal_null_exactly():
    key = {
        "emails": {
            "E001": {
                "line_items": [
                    {"id": "E001-1", "raw": "a", "sku": "SKU-A", "abstain": None, "qty": None, "qty_tol": 0}
                ]
            }
        }
    }
    pred_null = {"E001": {"line_items": [{"raw": "a", "sku": "SKU-A", "qty": None}]}}
    pred_nonnull = {"E001": {"line_items": [{"raw": "a", "sku": "SKU-A", "qty": 5}]}}
    assert "qty accuracy:            1.000" in _run(pred_null, key)
    assert "qty accuracy:            0.000" in _run(pred_nonnull, key)


def test_works_regardless_of_predictions_provenance():
    # Matching in isolation on the key's own lines vs. some other
    # extractor's lines -- score_match must not assume anything about how
    # predictions.json's raw strings were produced, only align by raw text.
    pred = {"OTHER_EMAIL_ID": {"line_items": [{"raw": "unrelated", "sku": "X", "qty": 1}]}}
    out = _run(pred)
    assert "uncovered key lines:     2" in out
