import json
from pathlib import Path

from rfq import emails as emailsmod
from rfq import extract

EMAILS_DIR = Path(__file__).resolve().parent.parent / "data" / "dev" / "emails"
LINES_KEY = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "dev" / "lines.json").read_text()
)


def _extract(eid: str) -> list[str]:
    e = emailsmod.parse_email(EMAILS_DIR / f"{eid}.eml")
    emailsmod.classify_one(e)
    return extract.extract_lines(e)


def test_E003_five_items_leading_connective_kept_when_glued():
    got = _extract("E003")
    assert got == LINES_KEY["E003"]
    assert got[1].startswith("and ")  # elliptical, anaphoric to item 1
    assert not got[2].startswith("also")  # dropped, self-contained + comma-preceded


def test_E006_three_items_wrapper_and_parenthetical_stripped():
    got = _extract("E006")
    assert got == LINES_KEY["E006"]


def test_E018_three_items_trailing_purpose_clause_dropped():
    got = _extract("E018")
    assert got == LINES_KEY["E018"]
    assert "for the kiln room" not in " ".join(got)
    assert "thanks" not in " ".join(got)


def test_prose_anchor_identifier_led_item():
    # E003-4: quantity trails the identifier ("TSM64072 x 40"), not a
    # leading digit -- a different anchor shape than every other item.
    got = extract.prose_anchor("please send TSM64072 x 40.")
    assert got == ["TSM64072 x 40"]


def test_prose_anchor_vague_quantifier_with_no_product_name():
    got = extract.prose_anchor("also a couple of the 1-inch brass ball valves for the ends of the runs.")
    assert got == ["a couple of the 1-inch brass ball valves"]
