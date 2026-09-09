import contextlib
import io
import json
from pathlib import Path

import pytest

from rfq import catalog as catmod
from tools.mutate import (
    ABLATIONS,
    FLIPPED_WRONG,
    MUTATIONS,
    _case_swap,
    _drop_material,
    _drop_size,
    _reflow_whitespace,
    _reorder_clause_punctuation,
    _swap_dash_style,
    _unicode_fraction_to_ascii,
    run_probe,
)

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def catalog():
    return catmod.load(DATA / "catalog.csv", DATA / "xref.csv")


@pytest.fixture(scope="module")
def answer_key():
    return json.loads((DATA / "dev" / "answer_key.json").read_text())


# --- form-preserving mutations ---------------------------------------------


def test_reflow_whitespace_collapses_padding():
    assert _reflow_whitespace("10   NF/27937/567   3/4 EMT 10ft") == "10 NF/27937/567 3/4 EMT 10ft"


def test_unicode_fraction_to_ascii():
    assert _unicode_fraction_to_ascii("20 × ½ 90 elbows") == "20 × 1/2 90 elbows"


def test_swap_dash_style_round_trips():
    assert _swap_dash_style("6 - 3/4 brass") == "6 – 3/4 brass"
    assert _swap_dash_style("6 – 3/4 brass") == "6 - 3/4 brass"


def test_reorder_clause_punctuation_single_comma():
    assert _reorder_clause_punctuation("10 of the 3/4 tees, black iron") == (
        "10 of the 3/4 tees black iron,"
    )


def test_reorder_clause_punctuation_skips_multi_comma():
    assert _reorder_clause_punctuation("a, b, c") == "a, b, c"


def test_case_swap_round_trips():
    assert _case_swap("hello") == "HELLO"
    assert _case_swap("HELLO") == "hello"


# --- ablations --------------------------------------------------------------


def test_drop_material_removes_multiword_finish():
    assert _drop_material("add 10 of the 1/2 black iron caps") == "add 10 of the 1/2 caps"
    assert "zinc" not in _drop_material("400 – 3/8 flat washers zinc").lower()


def test_drop_size_preserves_a_leading_fraction():
    # Regression: the leading-quantity stripper used to eat the "3" of a
    # leading "3/4", leaving a meaningless "/4" -- which made the ablation a
    # whitespace-only no-op that then registered as a false "guess".
    out = _drop_size("3/4 ss ball valve, threaded             25")
    assert "3/4" not in out and "/4" not in out


def test_drop_size_removes_uppercase_metric_token():
    # Regression: the size regex lacked IGNORECASE, so "M12" survived.
    assert "M12" not in _drop_size("100,M12 x 40 hex bolt class 8.8")


def test_drop_size_leaves_metric_material_class_alone():
    # "8.8" is a material class in this catalog, not a size -- removing it
    # would silently make this a second, mislabelled material ablation.
    assert "8.8" in _drop_size("100,M12 x 40 hex bolt class 8.8")


# --- the property the harness exists to prove -------------------------------


def test_no_rewrite_produces_a_different_confident_sku(catalog, answer_key):
    """The headline invariant: across every form-preserving rewrite and
    every ablation, nothing resolves to a DIFFERENT confident sku.
    Degrading to an abstention is acceptable; naming the wrong part is not.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        results = run_probe(answer_key, catalog, detail=False)

    offenders = {
        name: [r[0] for r in outcomes[FLIPPED_WRONG]]
        for name, outcomes in results.items()
        if outcomes[FLIPPED_WRONG]
    }
    assert not offenders, f"a rewrite produced a different confident sku: {offenders}"


def test_probe_registry_is_complete():
    assert set(MUTATIONS) == {
        "reflow_whitespace",
        "unicode_fraction_to_ascii",
        "swap_dash_style",
        "reorder_clause_punctuation",
        "case_swap",
    }
    assert set(ABLATIONS) == {"drop_material", "drop_size"}
