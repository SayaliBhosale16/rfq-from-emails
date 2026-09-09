from pathlib import Path

from rfq import emails as emailsmod

EMAILS_DIR = Path(__file__).resolve().parent.parent / "data" / "dev" / "emails"


def _load_batch() -> dict[str, emailsmod.Email]:
    batch = {}
    for p in sorted(EMAILS_DIR.glob("*.eml")):
        e = emailsmod.parse_email(p)
        emailsmod.classify_one(e)
        batch[e.id] = e
    emailsmod.classify_batch(batch)
    return batch


def test_E005_vendor_quote_classified_and_excluded():
    batch = _load_batch()
    assert batch["E005"].classification == "quote_from_vendor"
    assert not emailsmod.is_rfq_email(batch["E005"])


def test_E007_attachment_promised_but_absent():
    batch = _load_batch()
    e = batch["E007"]
    assert e.attachments == []
    assert e.classification == "no_content"
    assert not emailsmod.is_rfq_email(e)


def test_E011_quoted_history_excluded_but_new_ask_kept():
    e = emailsmod.parse_email(EMAILS_DIR / "E011.eml")
    new_text, quoted_spans = emailsmod.split_new_vs_quoted(e.text)
    assert quoted_spans, "expected at least one excluded span"
    assert "that quote is approved" not in new_text
    assert "-----Original Message-----" not in new_text
    assert "separately, please quote the following" in new_text
    assert "NF/27937/567" in new_text
    assert "RL-77981" in new_text


def test_E012_untraceable_po_reference():
    batch = _load_batch()
    e = batch["E012"]
    assert e.classification == "untraceable"
    assert not emailsmod.is_rfq_email(e)


def test_E013_duplicate_of_E004_by_later_date():
    batch = _load_batch()
    assert batch["E004"].classification == "rfq"
    assert batch["E013"].classification == "duplicate"
    assert not emailsmod.is_rfq_email(batch["E013"])
    assert batch["E004"].date < batch["E013"].date


def test_E015_forward_origin_is_original_sender_not_isr():
    e = emailsmod.parse_email(EMAILS_DIR / "E015.eml")
    assert e.sender_domain == emailsmod.INTERNAL_DOMAIN
    assert e.forward_origin is not None
    assert e.forward_origin.sender_domain == "copperlineplumbing.com"
    body_from_span = e.text[e.forward_origin.body_span[0] : e.forward_origin.body_span[1]]
    assert "can you work this one up" not in body_from_span
    assert "EL 90 3/4 BRASS qty 15" in body_from_span


def test_E010_supersedes_E003():
    e003 = emailsmod.parse_email(EMAILS_DIR / "E003.eml")
    e010 = emailsmod.parse_email(EMAILS_DIR / "E010.eml")
    batch = {"E003": e003, "E010": e010}
    assert emailsmod.find_supersedes(e010, batch) == "E003"


def test_all_other_dev_emails_stay_rfq():
    batch = _load_batch()
    non_rfq_ids = {
        eid for eid, e in batch.items() if not emailsmod.is_rfq_email(e)
    }
    assert non_rfq_ids == {"E005", "E007", "E012", "E013"}
