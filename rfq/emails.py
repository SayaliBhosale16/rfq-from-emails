"""
- Raw .eml comes in here, it pulls apart the MIME parts, strips forwarded and quoted threads, and works out who actually sent it sender-domain scoping, rfq-vs-quote classification, dedup/amendment pairing. The resulting Email object is what extract.py sees, not the raw .eml.

Decoding approach ported from data/eml_to_json.py's _text() helper (stdlib
email.parser + policy.default already handles base64/quoted-printable,
including QP soft line breaks -- so a decoded body's physical newlines are
the sender's real line breaks, not 76-char-wrap artifacts).
"""

from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Literal

INTERNAL_DOMAIN = "northwind-supply.example"

Classification = Literal["rfq", "quote_from_vendor", "duplicate", "untraceable", "no_content"]

NON_RFQ_CLASSIFICATIONS = {"quote_from_vendor", "duplicate", "untraceable", "no_content"}


@dataclass
class Attachment:
    filename: str | None
    mime: str
    text: str | None


@dataclass
class ForwardOrigin:
    sender: str
    sender_domain: str
    body_span: tuple[int, int]


@dataclass
class Email:
    id: str
    sender: str
    sender_domain: str
    to: str | None
    subject: str
    date: datetime | None
    message_id: str | None
    in_reply_to: str | None
    references: list[str] = field(default_factory=list)
    text: str | None = None
    html: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    forward_origin: ForwardOrigin | None = None
    classification: Classification = "rfq"


def _text(part) -> str | None:
    if part.get_content_maintype() != "text":
        return None
    try:
        return part.get_content()
    except Exception:  # unknown charset / bad transfer encoding
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if payload is not None else None


def _domain_of(address: str | None) -> str:
    if not address or "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].lower()


def parse_email(path: Path) -> Email:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())

    _, sender_addr = email.utils.parseaddr(str(msg.get("from", "")))
    sender_addr = sender_addr.lower()

    date_header = msg.get("date")
    date = None
    if date_header:
        try:
            date = email.utils.parsedate_to_datetime(str(date_header))
        except (TypeError, ValueError):
            date = None

    references_raw = str(msg.get("references", "") or "")
    references = references_raw.split() if references_raw else []

    e = Email(
        id=path.stem,
        sender=sender_addr,
        sender_domain=_domain_of(sender_addr),
        to=str(msg.get("to")) if msg.get("to") else None,
        subject=str(msg.get("subject", "")) if msg.get("subject") else "",
        date=date,
        message_id=str(msg.get("message-id")) if msg.get("message-id") else None,
        in_reply_to=str(msg.get("in-reply-to")) if msg.get("in-reply-to") else None,
        references=references,
    )

    for part in msg.walk():
        if part.is_multipart():
            continue
        ct = part.get_content_type()
        if part.get_content_disposition() == "attachment" or part.get_filename():
            e.attachments.append(
                Attachment(filename=part.get_filename(), mime=ct, text=_text(part))
            )
        elif ct == "text/plain" and e.text is None:
            e.text = _text(part)
        elif ct == "text/html" and e.html is None:
            e.html = _text(part)

    # forward_origin only means something for mail sent BY Northwind: an
    # external sender's own reply chain can just as easily contain an
    # embedded From:/Sent:/Subject: block (E011's quoted-history old quote
    # from Dana looks identical in shape) -- gating here, not just at the
    # use site, keeps the field's meaning unambiguous everywhere it's read.
    if e.sender_domain == INTERNAL_DOMAIN:
        e.forward_origin = _detect_forward_origin(e)
    return e


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Requires a literal "#" so an ordinary "please quote" doesn't false-positive
# (E005: "Valvex Quote #VQ-20811 - ball valves for Northwind stock").
_QUOTE_NUM_RE = re.compile(r"\bquote\s*#\s*[a-z0-9-]+", re.IGNORECASE)

_ATTACH_PROMISE_RE = re.compile(r"\b(attached|attachment)\b", re.IGNORECASE)

# "same as PO 4471 ... double it" -- references an earlier order this batch
# has no record of (E012). Not exercised on dev data: a customer mixing an
# untraceable reference with a fresh, concrete ask in the same email --
# documented as a known simplification in DECISIONS.md.
_UNTRACEABLE_RE = re.compile(
    r"\bsame as\b.{0,20}\b(?:po|order|invoice|quote)\b\s*#?\s*[\w-]+", re.IGNORECASE
)

_FORWARD_HEADER_RE = re.compile(
    r"^From:\s*(?P<from>.+)\n"
    r"Sent:\s*.+\n"
    r"(?:To:\s*.+\n)?"
    r"(?:Cc:\s*.+\n)?"
    r"Subject:\s*.+$",
    re.MULTILINE,
)
_EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _is_vendor_quote(e: Email) -> bool:
    haystack = f"{e.subject}\n{e.text or ''}"
    if not _QUOTE_NUM_RE.search(haystack):
        return False
    body = e.text or ""
    return body.count("$") >= 2


def _is_no_content(e: Email) -> bool:
    if e.attachments:
        return False
    body = e.text or ""
    return bool(_ATTACH_PROMISE_RE.search(body))


def _is_untraceable(e: Email) -> bool:
    body = e.text or ""
    return bool(_UNTRACEABLE_RE.search(body))


def _detect_forward_origin(e: Email) -> ForwardOrigin | None:
    """Find an embedded Outlook-style 'From:/Sent:/[To:]/Subject:' header
    block (E015's forward is plain-text, not a nested message/rfc822 MIME
    part). Only meaningful when e itself was sent from inside Northwind.
    """
    text = e.text or ""
    match = _FORWARD_HEADER_RE.search(text)
    if not match:
        return None
    addr_match = _EMAIL_ADDR_RE.search(match.group("from"))
    if not addr_match:
        return None
    sender = addr_match.group(0).lower()
    return ForwardOrigin(
        sender=sender,
        sender_domain=_domain_of(sender),
        body_span=(match.end(), len(text)),
    )


def classify_one(e: Email) -> None:
    """Single-email classification rules (mutates e.classification in place).
    Batch-level rules (duplicate, untraceable) run separately in classify_batch.
    """
    if e.sender_domain != INTERNAL_DOMAIN and _is_vendor_quote(e):
        e.classification = "quote_from_vendor"
        return
    if _is_no_content(e):
        e.classification = "no_content"
        return
    # Internal forward: sender_domain == INTERNAL_DOMAIN and a forward
    # header block was found. Classification stays "rfq" -- forwards are
    # still legitimate requests -- but forward_origin (already computed in
    # parse_email) is what scopes xref lookups downstream, not e.sender_domain.
    e.classification = "rfq"


def classify_batch(emails: dict[str, Email]) -> None:
    """Batch-level rules: untraceable external references, and duplicate
    detection by later Date: among byte-identical bodies. Only touches
    emails still classified "rfq" by classify_one.
    """
    for e in emails.values():
        if e.classification != "rfq":
            continue
        if _is_untraceable(e):
            e.classification = "untraceable"

    groups: dict[tuple[str, str], list[Email]] = {}
    for e in emails.values():
        if e.classification != "rfq":
            continue
        body = (e.text or "").strip()
        if not body:
            continue
        key = (e.sender, body)
        groups.setdefault(key, []).append(e)

    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda e: e.date or datetime.min.replace(tzinfo=None))
        for dup in group[1:]:
            dup.classification = "duplicate"


def is_rfq_email(e: Email) -> bool:
    return e.classification not in NON_RFQ_CLASSIFICATIONS


# ---------------------------------------------------------------------------
# Quoted-history exclusion (E011: approve an old quote, then ask something new)
# ---------------------------------------------------------------------------

_ORIGINAL_MESSAGE_RE = re.compile(r"^-{3,}\s*Original Message\s*-{3,}", re.IGNORECASE | re.MULTILINE)
# Gmail/plain-text reply-quoting style (E010: "On Wed, ... wrote:" followed
# by "> "-prefixed lines) -- a different shape than Outlook's
# "-----Original Message-----" block, needs its own marker.
_GMAIL_QUOTE_INTRO_RE = re.compile(r"^On .+ wrote:\s*$", re.MULTILINE)
_REANCHOR_RE = re.compile(
    r"\bseparately\b.{0,40}\bplease quote\b|\bplease quote the following\b",
    re.IGNORECASE,
)


def split_new_vs_quoted(text: str | None) -> tuple[str, list[tuple[int, int]]]:
    """Returns (new_text, quoted_spans). quoted_spans are (start, end) offsets
    into the original text that are quoted history / prior-approval
    commentary and must never reach extract.py's readers.
    """
    if not text:
        return "", []
    quoted_spans: list[tuple[int, int]] = []
    end = len(text)
    om_match = _ORIGINAL_MESSAGE_RE.search(text)
    if om_match and om_match.start() < end:
        end = om_match.start()
    gmail_match = _GMAIL_QUOTE_INTRO_RE.search(text)
    if gmail_match and gmail_match.start() < end:
        end = gmail_match.start()
    if end < len(text):
        quoted_spans.append((end, len(text)))
    start = 0
    reanchor_match = _REANCHOR_RE.search(text, 0, end)
    if reanchor_match:
        if reanchor_match.start() > 0:
            quoted_spans.append((0, reanchor_match.start()))
        start = reanchor_match.start()
    return text[start:end], quoted_spans


# ---------------------------------------------------------------------------
# Amendment / dedup pairing (best-effort, unscored per README)
# ---------------------------------------------------------------------------


def find_supersedes(e: Email, all_emails: dict[str, Email]) -> str | None:
    """Resolves in_reply_to/references against this batch's Message-IDs.
    Best-effort: returns None rather than guessing on ambiguity.
    """
    candidates = [e.in_reply_to] if e.in_reply_to else []
    candidates += e.references
    for other in all_emails.values():
        if other.id == e.id or not other.message_id:
            continue
        if other.message_id in candidates:
            return other.id
    return None
