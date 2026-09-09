"""email -> list[str] verbatim lines.

-  one per item in line / one per table row, a sentence or bullet list, one per physical line when no other structure is present.

GOLDEN RULE: never call any rfq.normalize.fold_* function on a string headed
for output. Every reader below slices/rstrips the ORIGINAL decoded text.
Local, extract-only regexes decide line/item boundaries; they never share
code with the comparison path (normalize.py / parse.py).
"""

from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser

from rfq.emails import Email, find_supersedes, is_rfq_email, split_new_vs_quoted

_HEADER_WORDS = {"qty", "quantity", "description", "part", "item", "sku"}


def _looks_like_header_row(cells: list[str]) -> bool:
    lowered = [c.strip().lower() for c in cells]
    return any(word in lowered for word in _HEADER_WORDS)


# ---------------------------------------------------------------------------
# Trailing-commentary / parenthetical dimension-content test
# ---------------------------------------------------------------------------

# A trailing clause can be dash-separated (E002) or comma-separated
# (E006-3: "12 pairs of large nitrile gloves, the guys keep loosing them...")
# -- same test either way, the word-count guard is what keeps a short,
# legitimate comma-attached modifier like E003-3's ", black iron" from
# ever being mistaken for commentary.
_DASH_SEGMENT_RE = re.compile(r"\s[-–—]\s|,\s*")
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
_PAREN_RE = re.compile(r"\([^()]*\)")
_HAS_DIMENSION_RE = re.compile(r"\d+/\d+|\d+\.\d+|\d+\"")


def _segment_is_droppable(segment: str) -> bool:
    stripped = segment.strip()
    if not stripped:
        return True
    if not stripped[0].islower():
        return False
    if len(stripped.split()) < 5:
        return False
    return not _HAS_DIMENSION_RE.search(stripped)


def drop_trailing_commentary(line: str) -> str:
    """One dimension-content test governs both dash-clauses and trailing
    parentheticals: drop a trailing segment iff it starts lowercase, is >=5
    words, and contains no dimension token; otherwise keep it whole.

    Verified directly against dev data: E006-1's parenthetical "(the blue
    stuff we got from you before)" drops (no dimension) while E002-2's
    "(.5 in brass ball valve)" keeps (has one) -- this is ONE rule, not a
    separate "dash rule" and "parenthetical rule".
    """
    working = line
    trailing_paren_match = _TRAILING_PAREN_RE.search(working)
    paren_content = None
    if trailing_paren_match:
        full = trailing_paren_match.group(0).strip()
        # test the CONTENT of the parenthetical, not the "(" character itself
        paren_content = full[1:-1] if full.startswith("(") and full.endswith(")") else full
        working = working[: trailing_paren_match.start()]

    parts = _DASH_SEGMENT_RE.split(working)
    seps = _DASH_SEGMENT_RE.findall(working)
    kept = [parts[0]]
    for sep, seg in zip(seps, parts[1:]):
        if _segment_is_droppable(seg):
            break
        kept.append(sep)
        kept.append(seg)
    result = "".join(kept)

    if paren_content is not None and not _segment_is_droppable(paren_content):
        result = result + line[trailing_paren_match.start() : trailing_paren_match.end()]

    return result


def _protect_parens(s: str) -> tuple[str, dict[str, str]]:
    """Replace each (...) span with an opaque placeholder so a comma or
    period INSIDE a parenthetical (E003-4: "(thats our number, you guys
    should have it on file from the Parkview job)") never gets mistaken
    for an item/sentence boundary during prose anchoring.
    """
    placeholders: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        key = f"\x00{len(placeholders)}\x00"
        placeholders[key] = m.group(0)
        return key

    return _PAREN_RE.sub(repl, s), placeholders


def _restore_parens(s: str, placeholders: dict[str, str]) -> str:
    for key, val in placeholders.items():
        s = s.replace(key, val)
    return s


# ---------------------------------------------------------------------------
# Structural readers -- each returns None (not []) when the shape isn't
# recognized, so dispatch can fall through cleanly to the next reader.
# ---------------------------------------------------------------------------


def read_csv_attachment(text: str) -> list[str] | None:
    if not text or "," not in text:
        return None
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        return None
    if _looks_like_header_row(rows[0]):
        rows = rows[1:]
    return [",".join(cell.strip() for cell in row) for row in rows]


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_text: list[str] = []
        self._row: list[str] = []
        self._table: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_text = []

    def handle_endtag(self, tag):
        if tag == "table" and self._in_table:
            self._in_table = False
            if self._table:
                self.tables.append(self._table)
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row:
                self._table.append(self._row)
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._row.append("".join(self._cell_text).strip())

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text.append(data)


def read_html_table(html: str) -> list[str] | None:
    if not html:
        return None
    parser = _TableHTMLParser()
    parser.feed(html)
    if not parser.tables:
        return None
    table = parser.tables[0]
    rows = [r for r in table if any(c for c in r)]
    if len(rows) < 2:
        return None
    if _looks_like_header_row(rows[0]):
        rows = rows[1:]
    return [" | ".join(row) for row in rows]


_ROW_LEAD_QTY_RE = re.compile(r"^\s*\d+")
_ROW_TRAIL_QTY_RE = re.compile(r"\d+(?:\s*[a-zA-Z]+)?\s*$")


def read_aligned_table(text: str) -> list[str] | None:
    if not text:
        return None
    lines = text.splitlines()
    header_idx = None
    header_positions: dict[str, int] = {}
    for i, line in enumerate(lines):
        found = {}
        for word in _HEADER_WORDS:
            m = re.search(rf"\b{word}\b", line, re.IGNORECASE)
            if m:
                found[word] = m.start()
        if len(found) >= 2:
            header_idx = i
            header_positions = found
            break
    if header_idx is None:
        return None

    qty_leading = True
    if "qty" in header_positions or "quantity" in header_positions:
        qty_pos = header_positions.get("qty", header_positions.get("quantity"))
        other_positions = [v for k, v in header_positions.items() if k not in ("qty", "quantity")]
        if other_positions and qty_pos > min(other_positions):
            qty_leading = False

    # rstrip only the newline/CR -- interior padding is byte-verbatim, and
    # since real content reaches the end of the line in both column orders
    # there's no trailing padding to strip either.
    data_lines: list[str] = []
    for line in lines[header_idx + 1 :]:
        if not line.strip():
            if data_lines:
                break
            continue
        if qty_leading:
            matches = bool(_ROW_LEAD_QTY_RE.match(line))
        else:
            matches = bool(_ROW_TRAIL_QTY_RE.search(line.rstrip()))
        if not matches:
            break
        data_lines.append(line.rstrip("\r\n"))
    if not data_lines:
        return None
    return data_lines


_NUMBERED_RE = re.compile(r"^\s*\d+\)\s*(.*)$")
_BULLET_RE = re.compile(r"^\s*•\s*(.*)$")
_DASH_BULLET_RE = re.compile(r"^\s*-\s+(.*)$")

# A numbered line ("N) ...") is unambiguous about where the item starts and
# ends -- the numbering itself resolves any boundary question, so it's kept
# fully atomic with no further trimming (E014 item 17's parenthetical
# "(under $25/ea if at all possible, that is what we paid last year)" stays
# whole even though its content alone wouldn't pass the dimension test --
# there's simply no ambiguity left for that test to resolve). A bullet/dash
# marker only tells you where an item STARTS, not where it ends, so the
# customer can and does append an editorializing aside after the real
# content with no further punctuation (E002 items 2 and 4) -- those still
# need drop_trailing_commentary.
_BULLET_PATTERNS = (
    (_NUMBERED_RE, False),
    (_BULLET_RE, True),
    (_DASH_BULLET_RE, True),
)


def read_numbered_or_bulleted_list(text: str) -> list[str] | None:
    if not text:
        return None
    lines = text.splitlines()
    for pattern, trim in _BULLET_PATTERNS:
        captured = [m.group(1) for m in (pattern.match(line) for line in lines) if m]
        if len(captured) >= 2:
            if trim:
                return [drop_trailing_commentary(item) for item in captured]
            return captured
    return None


_LEADING_DIGIT_RE = re.compile(r"^\s*\d")
_FRACTION_RE = re.compile(r"\d+/\d+")


def _qualifies_as_plain_item(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(_LEADING_DIGIT_RE.match(stripped) or _FRACTION_RE.search(stripped))


def read_plain_lines(text: str) -> list[str] | None:
    """One physical line = one item, for emails with no bullet/number marker
    at all (E001, E015, E016). Engages on the LONGEST contiguous run of
    qualifying lines (>=2) so greeting/signature lines around the item
    block are excluded without any separate quoted/signature detection.
    """
    if not text:
        return None
    lines = text.splitlines()
    best_run: list[str] = []
    current: list[str] = []
    for line in lines:
        if _qualifies_as_plain_item(line):
            current.append(line.rstrip("\r\n"))
        else:
            if len(current) > len(best_run):
                best_run = current
            current = []
    if len(current) > len(best_run):
        best_run = current
    if len(best_run) < 2:
        return None
    return best_run


# ---------------------------------------------------------------------------
# Prose anchorer (E003/E006/E018) -- the hard 20%. Scans the WHOLE text for
# quantity-shaped anchor positions (never pre-splits on commas, since a
# comma inside an item's own description -- E003-3's "3/4 tees, black
# iron" -- is not an item boundary), then derives each item's span from
# anchor to anchor, fixing up the connective glued onto that boundary.
#
# This is the explicitly fuzzy, judgment-call policy CLAUDE.md flags as
# "not a clean grammar" -- restated as a named policy in DECISIONS.md.
# ---------------------------------------------------------------------------

_ANCHOR_RE = re.compile(
    r"\d+(?:\.\d+)?\s+(?:of\b|meters?\b|metres?\b|pairs?\b|reels?\b|boxes?\b|pcs\b|rolls?\b|ea\b|\d+[a-zA-Z]*\b)"
    r"|a\s+couple(?:\s+of)?\b|a\s+few\b|several\b|some\b"
    r"|[a-zA-Z]{2,6}\d{3,7}\s+x\s+\d+",
    re.IGNORECASE,
)

# An anchor's own content, once trailing junk is stripped, is "elliptical"
# (anaphoric to the previous item, has no product noun of its own) only
# when it's nothing more than a bare "N of the <size>" -- e.g. E003-2's
# "20 of the 3/4″" needs item 1's "black iron 90s" to mean anything.
# Contrast E018-3's "2 15A single pole breakers", which names its own
# product and is NOT elliptical even though it's also glued with no comma.
_ELLIPTICAL_RE = re.compile(r"^\d+(?:\.\d+)?\s+of\s+the\s+[^\s,;]+\s*$", re.IGNORECASE)
_LEADING_CONNECTIVE_BACK_RE = re.compile(r"\b(?:and|also)\s+$", re.IGNORECASE)

# "and"/"also"/"plus" directly introducing a bare number ("...job) plus 100
# hex cap screws...") is a valid item boundary even when the description
# that follows uses no keyword _ANCHOR_RE recognizes (E003-5: "100 hex cap
# screws" has no "of"/"pairs"/etc. after the number at all).
_CONNECTIVE_DIGIT_RE = re.compile(r"\b(?:and|also|plus)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)

# Distinguish a real sentence-ending period from a decimal point by what
# FOLLOWS it, not what precedes it -- "40." at the end of a clause and
# "0.500" both have a digit right before the ".", but only the decimal
# has a digit right after it too.
_SENTENCE_END_RE = re.compile(r"\.(?!\d)")
_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")

# A polite-request wrapper ("also can you add", "can you please quote")
# always ends up dangling on the END of the PRECEDING item's span, since
# the following item's span starts cleanly at its own anchor -- so this
# is anchored at $, not ^ (E006-3: "...runs, also can you add 12 pairs...").
_TRAILING_WRAPPER_RE = re.compile(
    r"\s*[,;]?\s*(?:and\s+|also\s+)?(?:can|could)\s+(?:you|we)\s+(?:please\s+)?"
    r"(?:add|get|quote|include|throw in|send)\s*$",
    re.IGNORECASE,
)
_TRAILING_DANGLE_RE = re.compile(r"\s*[,;]?\s*(?:and|also|plus)\s*$", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r"\s*[,;]\s*$")
_TRAILING_PURPOSE_RE = re.compile(r"\s+for\s+the\s+.*$", re.IGNORECASE)


def _strip_trailing_junk(s: str) -> str:
    s = _TRAILING_WRAPPER_RE.sub("", s)
    s = _TRAILING_DANGLE_RE.sub("", s)
    s = _TRAILING_PURPOSE_RE.sub("", s)
    s = _TRAILING_COMMA_RE.sub("", s)
    return s.strip()


def prose_anchor(text: str) -> list[str]:
    if not text:
        return []
    protected, placeholders = _protect_parens(text)

    anchor_starts = {m.start() for m in _ANCHOR_RE.finditer(protected)}
    anchor_starts.update(m.start(1) for m in _CONNECTIVE_DIGIT_RE.finditer(protected))
    anchors = sorted(anchor_starts)
    if not anchors:
        return []

    effective_starts: list[int] = []
    for idx, start in enumerate(anchors):
        eff_start = start
        back_match = _LEADING_CONNECTIVE_BACK_RE.search(protected[:start])
        if back_match:
            next_raw = anchors[idx + 1] if idx + 1 < len(anchors) else len(protected)
            core = _strip_trailing_junk(protected[start:next_raw])
            if _ELLIPTICAL_RE.match(core):
                eff_start = back_match.start()
        effective_starts.append(eff_start)

    items = []
    for idx, start in enumerate(anchors):
        end = effective_starts[idx + 1] if idx + 1 < len(anchors) else len(protected)
        period_match = _SENTENCE_END_RE.search(protected, start)
        if period_match and period_match.start() < end:
            end = period_match.start()
        para_match = _PARAGRAPH_BREAK_RE.search(protected, start)
        if para_match and para_match.start() < end:
            end = para_match.start()
        span = protected[effective_starts[idx] : end]
        span = _restore_parens(span, placeholders)
        span = _strip_trailing_junk(span)
        span = drop_trailing_commentary(span).strip()
        if span:
            items.append(span)
    return items


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def extract_lines(email: Email) -> list[str]:
    if not is_rfq_email(email):
        return []

    body_text = email.text or ""
    if email.forward_origin is not None:
        start, end = email.forward_origin.body_span
        body_text = body_text[start:end]
    new_text, _ = split_new_vs_quoted(body_text)

    for att in email.attachments:
        if att.mime == "text/csv" or (att.filename or "").lower().endswith(".csv"):
            result = read_csv_attachment(att.text or "")
            if result is not None:
                return result

    if email.html:
        result = read_html_table(email.html)
        if result is not None:
            return result

    for reader in (read_aligned_table, read_numbered_or_bulleted_list, read_plain_lines):
        result = reader(new_text)
        if result is not None:
            return result

    return prose_anchor(new_text)


# ---------------------------------------------------------------------------
# Amendment merge (E010 over E003) -- check ORDER, not just contents.
# ---------------------------------------------------------------------------

# "make the 1/2 90s 80 not 50" is kept as ONE atomic correction fragment,
# never decomposed into an old-value/new-value pair -- captured whole and
# BEFORE prose_anchor ever sees the surrounding text, since a bare "50"/
# "80" inside it would otherwise register as spurious anchors of their own.
_CORRECTION_RE = re.compile(
    r"\bmake\s+the\s+.+?\s+\d+(?:\.\d+)?\s+not\s+\d+(?:\.\d+)?\b", re.IGNORECASE
)
_ADD_NEW_ITEM_RE = re.compile(r"\badd\s+\d+(?:\.\d+)?\s+.+", re.IGNORECASE)
# Amendment-specific idiom ("rest stays the same") -- meta-commentary about
# the REST of the order, not part of the new item's own description. Too
# short (4 words) to be caught by drop_trailing_commentary's >=5-word guard,
# and specific enough to this context that lowering that guard generally
# would risk dropping legitimate short modifiers elsewhere (E003-3's
# ", black iron").
_REST_SAME_RE = re.compile(r"\s*,?\s*(?:the\s+)?rest\s+stays?\s+the\s+same.*$", re.IGNORECASE)
_DELTA_SIGNAL_RE = re.compile(
    r"\bmake\s+the\b.+\bnot\b|\brest\s+stays?\s+the\s+same\b|\beverything\s+else\s+the\s+same\b",
    re.IGNORECASE,
)

_AMENDMENT_STOPWORDS = {"the", "of", "and", "not", "make", "a", "an", "for", "to", "add"}
_AMENDMENT_TOKEN_RE = re.compile(r"[a-zA-Z0-9/]+")


def _amendment_tokens(s: str) -> set[str]:
    return {
        t.lower() for t in _AMENDMENT_TOKEN_RE.findall(s) if t.lower() not in _AMENDMENT_STOPWORDS
    }


def _find_matching_line(lines: list[str], fragment: str) -> int | None:
    """Best-effort token-overlap match: which original line is this
    correction clause talking about? The reply never repeats the original
    raw text verbatim, so this can't be an exact-string lookup.
    """
    frag_tokens = _amendment_tokens(fragment)
    best_idx, best_score = None, 0
    for i, line in enumerate(lines):
        score = len(frag_tokens & _amendment_tokens(line))
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def merge_amendment(original_lines: list[str], amendment_text: str) -> list[str]:
    """A reply that amends an earlier order (E010 over E003): a correction
    clause REPLACES the original line it targets, lines the reply doesn't
    mention are copied unchanged in their ORIGINAL position, and a
    genuinely new "add ..." item is appended at the end. Order is the
    thing under test here, not just contents.
    """
    result = list(original_lines)

    correction_match = _CORRECTION_RE.search(amendment_text)
    if correction_match:
        correction_fragment = correction_match.group(0)
        idx = _find_matching_line(result, correction_fragment)
        if idx is not None:
            result[idx] = correction_fragment

    add_match = _ADD_NEW_ITEM_RE.search(amendment_text)
    if add_match:
        tail = add_match.group(0)
        period_match = _SENTENCE_END_RE.search(tail)
        if period_match:
            tail = tail[: period_match.start()]
        tail = _REST_SAME_RE.sub("", tail)
        tail = _strip_trailing_junk(tail)
        tail = drop_trailing_commentary(tail).strip()
        if tail:
            result.append(tail)

    return result


def extract_batch(emails: dict[str, Email]) -> dict[str, list[str]]:
    """Extracts every email in the batch, then applies best-effort amendment
    merging for a reply that supersedes an earlier email in the same batch
    AND reads as a delta over it (a correction/"rest stays the same"
    idiom) rather than a freshly restated complete order -- per README,
    "if in doubt, emit the complete order," so a reply with no delta
    signal is left as its own independent extraction, never merged.
    """
    lines_by_id = {eid: extract_lines(e) for eid, e in emails.items()}

    for eid, e in emails.items():
        target = find_supersedes(e, emails)
        if target is None or target not in lines_by_id:
            continue
        raw_text = e.text or ""
        if _DELTA_SIGNAL_RE.search(raw_text):
            lines_by_id[eid] = merge_amendment(lines_by_id[target], raw_text)

    return lines_by_id
