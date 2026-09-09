import io
import contextlib

from tools.score_lines import score_lines


def _run(pred, key, detail=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        score_lines(pred, key, detail)
    return buf.getvalue()


def test_exact_match_perfect_score():
    key = {"E001": ["a", "b"]}
    out = _run(key, key)
    assert "missed lines:       0" in out
    assert "hallucinated lines: 0" in out
    assert "line recall:        1.000" in out
    assert "line precision:     1.000" in out


def test_missed_line_counted():
    pred = {"E001": ["a"]}
    key = {"E001": ["a", "b"]}
    out = _run(pred, key)
    assert "missed lines:       1" in out
    assert "hallucinated lines: 0" in out


def test_hallucinated_line_counted():
    pred = {"E001": ["a", "b", "c"]}
    key = {"E001": ["a", "b"]}
    out = _run(pred, key)
    assert "missed lines:       0" in out
    assert "hallucinated lines: 1" in out


def test_order_independent_within_email():
    pred = {"E001": ["b", "a"]}
    key = {"E001": ["a", "b"]}
    out = _run(pred, key)
    assert "missed lines:       0" in out
    assert "hallucinated lines: 0" in out


def test_works_on_any_conforming_file_regardless_of_provenance():
    # pred has an email the key doesn't, and vice versa -- scorer must not
    # crash, and must count both directions correctly.
    pred = {"E001": ["a"], "E999": ["z"]}
    key = {"E001": ["a"], "E002": []}
    out = _run(pred, key)
    assert "missed lines:       0" in out
    assert "hallucinated lines: 1" in out
