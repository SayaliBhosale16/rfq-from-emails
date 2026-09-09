"""One raw line -> typed Query (qty, unit word, size, material, family,
identifier tokens).

- From here it's per line. This reads each line into fields — quantity, size, material, thread, and any part-number tokens.

This is the comparison side, so folding via normalize.py is correct here -- unlike extract.py, which must never fold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rfq import normalize

_THREAD_WORD_RE = re.compile(r"\b(FNPT|MNPT|NPT|SWT)\b", re.IGNORECASE)
_GENERIC_THREADED_RE = re.compile(r"\bthreaded\b", re.IGNORECASE)

# size shapes, most-specific first: "1-1/4" (compound whole+fraction), "3/8"
# (fraction), ".5"/"0.500" (decimal, leading digit optional), "m12" (metric).
# Bare integer is deliberately NOT in here -- it's tried separately, LAST,
# as a distinct pass (see _find_size below), never in the same alternation:
# regex alternation only prefers an earlier alternative when multiple could
# match at the SAME position, not across different positions in the string,
# so a single combined pattern can't express "prefer a fraction anywhere in
# the text over a bare integer anywhere in the text".
_SIZE_TOKEN = r"\d+-\d+/\d+|\d+/\d+|\d*\.\d+|m\d+"
_SIZE_TOKEN_WITH_INT = rf"{_SIZE_TOKEN}|\d+"

# fastener shape: SIZE(-THREAD)? x LENGTH, e.g. "3/8-16 x 2-1/2", "1/2-13 x 2",
# "M12 x 40". THREAD is a bare pitch number here, never NPT-style words.
_FASTENER_RE = re.compile(
    rf"(?P<size>{_SIZE_TOKEN_WITH_INT})(?:\s*-\s*(?P<thread>\d+(?:\.\d+)?))?\s*x\s*(?P<length>{_SIZE_TOKEN_WITH_INT})",
    re.IGNORECASE,
)

_FRACTION_OR_DECIMAL_RE = re.compile(_SIZE_TOKEN, re.IGNORECASE)
_METRIC_SIZE_RE = re.compile(r"m\d+", re.IGNORECASE)
_BARE_INT_RE = re.compile(r"\d+")
# "N square box/cover" (E014-32: "4 sq box 2-1/8 deep") -- the square
# dimension "4" is the real size (folds to catalog's "4x4"->"4"); "2-1/8"
# is a depth that's only ever mentioned in the description text, not a
# structured column, so it's deliberately not extracted as anything here.
_SQUARE_SIZE_RE = re.compile(r"(\d+)\s*sq(?:uare)?\b", re.IGNORECASE)
# Wire gauge: the number right before "awg" (E018-2: "14 AWG THHN... 500'
# reels" -- the AWG number, not the 500' spool length, is the catalog size).
_AWG_RE = re.compile(r"(\d+)\s*awg", re.IGNORECASE)

# A number immediately preceded by "$" or followed by "/ea"/"/ft" etc. is a
# price fragment, never a filter constraint (E014-17: "under $25/ea").
_PRICE_FRAGMENT_RE = re.compile(r"\$\s*\d+(?:\.\d+)?(?:/\w+)?|\d+(?:\.\d+)?/(?:ea|ft)\b", re.IGNORECASE)

_QTY_PHRASE_RE = re.compile(r"\bqty\.?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
# "make the 1/2 90s 80 not 50" (E010-1): the NEW quantity is the number
# right before "not", never the old one after it -- tried before the
# plain trailing check, which would otherwise grab the trailing "50".
_CORRECTION_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s+not\s+\d+(?:\.\d+)?", re.IGNORECASE)
_LEADING_QTY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)(?!/)\b")
_IDENTIFIER_QTY_RE = re.compile(r"\b[a-zA-Z]{2,6}\d{2,7}\s+x\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)
# "add 10 of the 1/2 black iron caps" (E010-6): a self-contained imperative
# verb glued to its own qty -- distinct from _CONNECTIVE_LED_QTY_RE below,
# which is for elliptical "and"/"also" continuations, not fresh items.
_ACTION_LED_QTY_RE = re.compile(
    r"^\s*(?:add|include|send|get)\s+(\d+(?:\.\d+)?)(?!/)\b", re.IGNORECASE
)
# "and 20 of the 3/4″" (E003-2): the qty is glued right after a bare
# leading "and"/"also" with nothing else before it -- narrow on purpose
# (a generic "any standalone number anywhere" fallback is too eager: it
# grabs the "1" out of "1/2" or the "13" out of "1/2-13" just as readily).
_CONNECTIVE_LED_QTY_RE = re.compile(r"^\s*(?:and|also)\s+(\d+(?:\.\d+)?)(?!/)\b", re.IGNORECASE)
# "2 bx"/"2 reels" at the very end (E009-3, E017-2): the plain trailing
# check below requires nothing but whitespace after the digit, which
# misses a qty immediately followed by its own unit word.
_TRAILING_QTY_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s+([a-z]+)\s*$", re.IGNORECASE)
_TRAILING_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*$")

_TWO_OPTIONS_RE = re.compile(
    rf"({_SIZE_TOKEN_WITH_INT})\s+or\s+({_SIZE_TOKEN_WITH_INT})", re.IGNORECASE
)

# Tokens shaped like a customer part number or our own sku: starts with
# letters, mostly alnum/dash/slash, with enough digits to be confident it's
# an identifier and not a real dimension -- "M12" (2 digits) must NOT match
# this (it's a real metric size), but "TSM64072" (5 digits), "AF-04202" (5),
# "RL-77981" (5), "NF/27937/567" (8) all should.
_IDENTIFIER_SHAPE_RE = re.compile(r"^[A-Za-z]{1,4}[\w/\-]*\d[\w/\-]*$")


def _digit_count(s: str) -> int:
    return sum(c.isdigit() for c in s)


def _looks_like_identifier(token: str, min_digits: int) -> bool:
    return bool(_IDENTIFIER_SHAPE_RE.match(token)) and _digit_count(token) >= min_digits


@dataclass
class Query:
    raw: str
    qty_stated: float | None = None
    qty_is_vague: bool = False
    unit_word: str | None = None
    size: str | None = None
    length: str | None = None
    material: str | None = None
    thread: str | None = None
    pole: str | None = None
    family: str | None = None
    identifier_tokens: list[str] = field(default_factory=list)
    named_options: list[dict] = field(default_factory=list)
    stray_numbers: list[str] = field(default_factory=list)


def _strip_price_fragments(folded: str) -> str:
    return _PRICE_FRAGMENT_RE.sub(" ", folded)


_MEASURE_UNITS = {"FT", "M"}


def _resolve_unit_word(
    folded_no_price: str, qty_span: tuple[int, int] | None, whole_text_unit_word: str | None
) -> str | None:
    """A continuous-measure unit (FT/M) only counts as the qty's own unit
    if it's the word immediately following the qty match -- otherwise it's
    almost certainly describing something else's packaging, not the
    customer's purchase intent (E014-31: "1 - 12 AWG THHN white 500 ft" --
    "500 ft" is the spool's own fixed length, not a stated foot-quantity;
    the real qty is bare "1"). A countable pack unit (reel/box/pair/dozen)
    has no such requirement -- it names how the qty itself is packaged,
    wherever in the line it appears (E018-2: "10 14 AWG THHN red 500'
    reels" -- "reels" is at the far end, and still means 10 reels).
    """
    if whole_text_unit_word not in _MEASURE_UNITS:
        return whole_text_unit_word
    if qty_span is None:
        return None
    tail = folded_no_price[qty_span[1] :]
    first_word = re.match(r"\s*([a-z]+)", tail)
    if first_word and normalize.fold_unit_word(first_word.group(1)) == whole_text_unit_word:
        return whole_text_unit_word
    return None


def _parse_qty(
    folded_no_price: str,
) -> tuple[float | None, bool, str | None, tuple[int, int] | None]:
    """Returns (qty, is_vague, unit_word, matched_span). matched_span is the
    position of the QUANTITY NUMBER ITSELF within folded_no_price, so the
    caller can blank it out before size/length extraction -- otherwise a
    leading qty like "6" in "6 - 3/4 brass gate valves" gets mistaken for
    the size (there's no dimension-vs-quantity distinction a bare regex can
    make from content alone; position relative to the qty match is what
    disambiguates it).
    """
    whole_text_unit_word = normalize.fold_unit_word(folded_no_price)
    if whole_text_unit_word == normalize.VAGUE_UNIT:
        return None, True, whole_text_unit_word, None

    for pattern, use_match in (
        (_QTY_PHRASE_RE, "search"),
        (_CORRECTION_QTY_RE, "search"),
        (_LEADING_QTY_RE, "match"),
        (_IDENTIFIER_QTY_RE, "search"),
        (_ACTION_LED_QTY_RE, "match"),
        (_CONNECTIVE_LED_QTY_RE, "match"),
    ):
        m = pattern.search(folded_no_price) if use_match == "search" else pattern.match(folded_no_price)
        if m:
            unit_word = _resolve_unit_word(folded_no_price, m.span(1), whole_text_unit_word)
            return float(m.group(1)), False, unit_word, m.span(1)

    m = _TRAILING_QTY_UNIT_RE.search(folded_no_price)
    if m and normalize.fold_unit_word(m.group(2)):
        unit_word = _resolve_unit_word(folded_no_price, m.span(1), whole_text_unit_word)
        return float(m.group(1)), False, unit_word, m.span(1)

    m = _TRAILING_QTY_RE.search(folded_no_price)
    if m:
        unit_word = _resolve_unit_word(folded_no_price, m.span(1), whole_text_unit_word)
        return float(m.group(1)), False, unit_word, m.span(1)

    return None, False, None, None


def _blank_identifier_tokens(raw: str) -> str:
    """Blank out (with spaces, preserving length) any RAW-string token that
    looks identifier-shaped with enough digits to be confident -- run before
    folding, so a customer number like "TSM64072" or "NF/27937/567" never
    gets mistaken for a size/family token during attribute extraction. Only
    fires on the id-lookup FAILURE path in practice: resolve() already tries
    identifier lookup first and returns immediately on a hit, so this only
    matters once that's already failed for every candidate.
    """
    tokens = list(re.finditer(r"[^\s|]+", raw))
    chars = list(raw)
    for m in tokens:
        tok = m.group(0).strip(",")
        if _looks_like_identifier(tok, min_digits=4):
            for i in range(m.start(), m.end()):
                if not chars[i].isspace():
                    chars[i] = " "
    return "".join(chars)


def _identifier_candidates(raw: str) -> list[str]:
    """Whitespace/pipe-delimited tokens (plus adjacent pairs, to catch a
    two-word xref key like "IP 58899-0336") that look identifier-shaped.
    Extracted from the RAW string, not folded -- identifiers are opaque to
    unicode/fraction folding and case doesn't matter to catalog.py's
    case-insensitive lookup. Lower digit threshold than the blanking pass:
    a failed lookup attempt is harmless, so it's fine to try more candidates.
    """
    tokens = [t for t in re.split(r"[\s|]+", raw.strip()) if t]
    candidates = []
    for i, tok in enumerate(tokens):
        stripped = tok.strip(",")
        if _looks_like_identifier(stripped, min_digits=3):
            candidates.append(stripped)
        if i + 1 < len(tokens):
            pair = f"{stripped} {tokens[i + 1].strip(',')}"
            candidates.append(pair)
    candidates.append(raw.strip())
    return candidates


def _find_size(text: str) -> str | None:
    """Size-token search, in priority order -- never a single combined
    regex, since alternation can't express "prefer a fraction anywhere over
    an integer anywhere" (see _SIZE_TOKEN's docstring above).
    """
    m = _SQUARE_SIZE_RE.search(text)
    if m:
        return normalize.fold_size(m.group(1))
    m = _AWG_RE.search(text)
    if m:
        return normalize.fold_size(m.group(1))
    m = _FRACTION_OR_DECIMAL_RE.search(text)
    if m:
        return normalize.fold_size(m.group(0))
    m = _METRIC_SIZE_RE.search(text)
    if m:
        return normalize.fold_size(m.group(0))
    m = _BARE_INT_RE.search(text)
    if m:
        return normalize.fold_size(m.group(0))
    return normalize.fold_size_or_variant(text)


def parse_line(raw: str) -> Query:
    raw_sans_ids = _blank_identifier_tokens(raw)
    folded = normalize.fold_text(raw_sans_ids)
    folded_no_price = _strip_price_fragments(folded)

    qty_stated, qty_is_vague, unit_word, qty_span = _parse_qty(folded_no_price)

    # Blank out the matched QUANTITY NUMBER before any other attribute
    # extraction -- otherwise "6" in "6 - 3/4 brass gate valves" gets
    # mistaken for the size, since content alone can't distinguish a
    # quantity from a dimension.
    text_for_attrs = folded_no_price
    if qty_span:
        start, end = qty_span
        text_for_attrs = folded_no_price[:start] + " " * (end - start) + folded_no_price[end:]

    material = normalize.fold_material(text_for_attrs)
    family = normalize.fold_family(text_for_attrs)
    pole = normalize.fold_pole(text_for_attrs)

    # "box" is ambiguous: a packaging unit ("2 boxes of nuts") almost
    # everywhere, but the PRODUCT itself for electrical boxes/covers
    # (E014-32: "4 sq box" names the item, not a multiplier) -- once family
    # resolution already claims the word for the product, it can't also be
    # the pack-count unit for this same line.
    if family == "BOX" and unit_word == "BX":
        unit_word = None
    thread_word_match = _THREAD_WORD_RE.search(text_for_attrs)
    thread_word = thread_word_match.group(1).upper() if thread_word_match else None
    if thread_word is None and _GENERIC_THREADED_RE.search(text_for_attrs):
        # "threaded" (E002-4, E017-1) names the connection TYPE as opposed
        # to solder/sweat (SWT) -- FNPT is the catalog's standard threaded
        # pipe-fitting connection, distinct from a valve's solder-end SWT
        # variant. Only a default when no MORE specific NPT word was found.
        thread_word = "FNPT"

    size: str | None = None
    length: str | None = None
    thread: str | None = thread_word

    fastener_match = _FASTENER_RE.search(text_for_attrs)
    if fastener_match:
        size = normalize.fold_size(fastener_match.group("size"))
        length = normalize.fold_size(fastener_match.group("length"))
        if thread is None and fastener_match.group("thread"):
            thread = fastener_match.group("thread")
    else:
        size = _find_size(text_for_attrs)

    named_options: list[dict] = []
    two_opt_match = _TWO_OPTIONS_RE.search(text_for_attrs)
    if two_opt_match:
        named_options = [
            {"size": normalize.fold_size(two_opt_match.group(1))},
            {"size": normalize.fold_size(two_opt_match.group(2))},
        ]

    stray_numbers = _PRICE_FRAGMENT_RE.findall(folded)

    return Query(
        raw=raw,
        qty_stated=qty_stated,
        qty_is_vague=qty_is_vague,
        unit_word=unit_word,
        size=size,
        length=length,
        material=material,
        thread=thread,
        pole=pole,
        family=family,
        identifier_tokens=_identifier_candidates(raw),
        named_options=named_options,
        stray_numbers=[s for s in stray_numbers if s],
    )
