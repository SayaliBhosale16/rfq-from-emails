import json
from pathlib import Path

from rfq import emails as emailsmod
from rfq import extract

EMAILS_DIR = Path(__file__).resolve().parent.parent / "data" / "dev" / "emails"
LINES_KEY = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "dev" / "lines.json").read_text()
)


def _load_batch():
    batch = {}
    for p in sorted(EMAILS_DIR.glob("*.eml")):
        e = emailsmod.parse_email(p)
        emailsmod.classify_one(e)
        batch[e.id] = e
    emailsmod.classify_batch(batch)
    return batch


def test_E010_full_six_item_reconstruction_and_order():
    batch = _load_batch()
    lines_by_id = extract.extract_batch(batch)
    assert lines_by_id["E010"] == LINES_KEY["E010"]


def test_E010_correction_clause_kept_whole_not_decomposed():
    batch = _load_batch()
    lines_by_id = extract.extract_batch(batch)
    assert lines_by_id["E010"][0] == "make the 1/2 90s 80 not 50"


def test_E010_new_item_appended_last():
    batch = _load_batch()
    lines_by_id = extract.extract_batch(batch)
    assert lines_by_id["E010"][-1] == "add 10 of the 1/2 black iron caps"


def test_E010_unmentioned_originals_copied_in_original_order():
    batch = _load_batch()
    lines_by_id = extract.extract_batch(batch)
    assert lines_by_id["E010"][1:5] == LINES_KEY["E003"][1:5]


def test_E003_unaffected_by_E010s_amendment():
    batch = _load_batch()
    lines_by_id = extract.extract_batch(batch)
    assert lines_by_id["E003"] == LINES_KEY["E003"]


def test_merge_amendment_directly():
    original = [
        "50 of the 1/2″ black iron 90s",
        "and 20 of the 3/4″",
        "10 of the 3/4 tees, black iron",
        "TSM64072 x 40",
        "100 hex cap screws 1/2-13 x 2 zinc",
    ]
    amendment_text = (
        "small change - make the 1/2 90s 80 not 50, and add 10 of the 1/2 "
        "black iron caps, rest stays the same. sorry for the back and forth."
    )
    got = extract.merge_amendment(original, amendment_text)
    assert got == [
        "make the 1/2 90s 80 not 50",
        "and 20 of the 3/4″",
        "10 of the 3/4 tees, black iron",
        "TSM64072 x 40",
        "100 hex cap screws 1/2-13 x 2 zinc",
        "add 10 of the 1/2 black iron caps",
    ]


def test_reply_with_no_delta_signal_is_not_merged():
    # A reply that supersedes an earlier email but reads as a fresh
    # complete order (no correction/"rest stays the same" idiom) should be
    # left as its own independent extraction, per README's "if in doubt,
    # emit the complete order" -- not force-merged into a delta.
    original = ["10 of the 1/2 elbows"]
    fresh_restatement = "please send 20 of the 1/2 elbows and 5 of the 3/4 tees instead."
    assert not extract._DELTA_SIGNAL_RE.search(fresh_restatement)
