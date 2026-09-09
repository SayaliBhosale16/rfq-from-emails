"""The one place that knows fraction/unicode/unit folding.

Used by catalog.py (indexing catalog.csv) and parse.py (parsing a raw query
line) so the two sides of a comparison can never drift apart. Never used by
extract.py's output path -- lines.json strings are byte-verbatim slices of
the source text, not the folded form.
"""

from __future__ import annotations

import re
import unicodedata

DASH_MAP = {
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
}

FRACTION_MAP = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

SYMBOL_MAP = {
    "×": "x",  # multiplication sign
    "°": "",  # degree sign
    "″": '"',  # double prime
    "′": "'",  # prime
}

_TRANSLATE_MAP = {**DASH_MAP, **FRACTION_MAP, **SYMBOL_MAP}
_TRANSLATE_TABLE = str.maketrans(_TRANSLATE_MAP)

_WS_RE = re.compile(r"\s+")


def fold_text(s: str) -> str:
    """Fold dashes/fractions/symbols + unicode-normalize, lowercase, collapse whitespace.

    Translate BEFORE NFKC: NFKC's own compatibility decomposition would beat
    us to some of these chars (e.g. it decomposes "″" DOUBLE PRIME into
    two separate PRIME marks, and "½" into "1⁄2" using a fraction
    slash we'd otherwise miss), so we fold the literal characters first and
    let NFKC clean up whatever's left.

    This is the comparison-side folding function. Never call it on a string
    that will be emitted as extraction output.
    """
    s = s.translate(_TRANSLATE_TABLE)
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = _WS_RE.sub(" ", s).strip()
    return s


# Sizes that appear in catalog.csv, canonicalized to their fraction form.
# fold_size falls back to decimal-equality comparison for anything not here,
# so an unseen decimal (e.g. "0.375") still folds correctly against
# a catalog size of "3/8".
_SIZE_DECIMALS = {
    "1/4": 0.25,
    "3/8": 0.375,
    "1/2": 0.5,
    "5/8": 0.625,
    "3/4": 0.75,
    "7/8": 0.875,
    "1": 1.0,
    "1-1/4": 1.25,
    "1-1/2": 1.5,
    "2": 2.0,
}

_FRACTION_TOKEN_RE = re.compile(r"^\d+(?:-\d+/\d+|/\d+)?$")


def _size_to_float(token: str) -> float | None:
    token = token.strip()
    if token in _SIZE_DECIMALS:
        return _SIZE_DECIMALS[token]
    if _FRACTION_TOKEN_RE.match(token):
        if "-" in token:
            whole, frac = token.split("-", 1)
            num, den = frac.split("/")
            return int(whole) + int(num) / int(den)
        if "/" in token:
            num, den = token.split("/")
            return int(num) / int(den)
        return float(token)
    try:
        return float(token)
    except ValueError:
        return None


def fold_size(s: str | None) -> str | None:
    """Canonicalize a size token to its fraction form, e.g. '.5'/'0.500'/'1/2' -> '1/2'.

    Returns None if s is None/empty or doesn't parse as a size at all.
    """
    if not s:
        return None
    folded = fold_text(s)
    match = re.search(r"\d*\.\d+|\d+(?:-\d+/\d+|/\d+)?", folded)
    if not match:
        return None
    token = match.group(0)
    value = _size_to_float(token)
    if value is None:
        return None
    for canon, canon_value in _SIZE_DECIMALS.items():
        if abs(value - canon_value) < 1e-6:
            return canon
    # Not one of the known catalog sizes -- keep whatever fraction/decimal
    # form parsed, so callers can still compare it via _size_to_float equality
    # against another unseen value.
    return token


MATERIAL_SYNONYMS = {
    "black iron": "BI",
    "blk iron": "BI",
    "brass": "BR",
    "stainless steel": "SS",
    "stainless": "SS",
    "18-8": "SS",
    "ss": "SS",
    "bi": "BI",
    "zinc plated": "ZP",
    "zinc": "ZP",
    "zp": "ZP",
    "plain steel": "PL",
    "pl": "PL",
    "cadmium": "CD",
    "cd": "CD",
    "galvanized": "GALV",
    "galv": "GALV",
    "nbr": "NBR",
    "buna": "NBR",
    "nitrile": "NIT",
    "leather": "LTH",
    "latex": "LTX",
    "epdm": "EPDM",
    "nylon": "NYL",
    "copper": "CU",
    "cu": "CU",
    "steel": "STL",
    "stl": "STL",
    "a2": "A2",
    "class 8.8": "8.8",
    "class 8": "8.8",
    "cl 8": "8.8",
    "grade 8.8": "8.8",
    "8.8": "8.8",
}

# Material-shaped phrases that are NOT in the catalog at all. Distinguishing
# "named an unknown material" from "named no material" matters: the former
# forces not_in_catalog (E014-19, E011-2 -- no PVC row exists anywhere in
# catalog.csv), the latter just proceeds without a material constraint.
MATERIAL_NAMED_BUT_UNKNOWN = "__UNKNOWN_MATERIAL__"

_KNOWN_BUT_UNCATALOGED_MATERIALS = {"pvc", "plastic", "poly", "aluminum", "aluminium"}

_MATERIAL_PHRASES_LONGEST_FIRST = sorted(
    MATERIAL_SYNONYMS.keys(), key=len, reverse=True
)


def fold_material(s: str | None) -> str | None:
    """Free-text material phrase -> catalog material code, or the
    MATERIAL_NAMED_BUT_UNKNOWN sentinel, or None if no material phrase found at all.
    """
    if not s:
        return None
    folded = fold_text(s)
    for phrase in _MATERIAL_PHRASES_LONGEST_FIRST:
        if re.search(rf"\b{re.escape(phrase)}\b", folded):
            return MATERIAL_SYNONYMS[phrase]
    for phrase in _KNOWN_BUT_UNCATALOGED_MATERIALS:
        if re.search(rf"\b{re.escape(phrase)}\b", folded):
            return MATERIAL_NAMED_BUT_UNKNOWN
    return None


def describe_unknown_material(folded_text: str) -> str | None:
    """Which uncataloged material phrase (already-folded text) triggered
    MATERIAL_NAMED_BUT_UNKNOWN -- for a human-checkable `why` message."""
    for phrase in _KNOWN_BUT_UNCATALOGED_MATERIALS:
        if re.search(rf"\b{re.escape(phrase)}\b", folded_text):
            return phrase
    return None


# Some catalog families have no numeric "size" at all -- the size COLUMN
# itself holds a letter size (gloves: S/M/L/XL) or a color name (wire
# nuts: "Yellow"), and a few families (safety glasses, hard hats, paint
# markers) don't even populate the size column -- the color only lives in
# the description/sku text. Folding both sides through the SAME function
# is what keeps a customer's "hard hat yellow" matching HH-YEL's
# description-derived key without a separate ad hoc "color" concept.
LETTER_SIZES = {
    "s": "S",
    "small": "S",
    "m": "M",
    "medium": "M",
    "l": "L",
    "large": "L",
    "xl": "XL",
    "extra large": "XL",
    "xxl": "XXL",
}
COLOR_WORDS = {
    "yellow": "YELLOW",
    "red": "RED",
    "blue": "BLUE",
    "gray": "GRAY",
    "grey": "GRAY",
    "orange": "ORANGE",
    "white": "WHITE",
    "black": "BLACK",
    "clear": "CLEAR",
    "smoke": "SMOKE",
    "amber": "AMBER",
    "green": "GREEN",
}
_LETTER_SIZE_WORDS_LONGEST_FIRST = sorted(LETTER_SIZES.keys(), key=len, reverse=True)
_COLOR_WORDS_LONGEST_FIRST = sorted(COLOR_WORDS.keys(), key=len, reverse=True)


def fold_color(s: str | None) -> str | None:
    if not s:
        return None
    folded = fold_text(s)
    for word in _COLOR_WORDS_LONGEST_FIRST:
        if re.search(rf"\b{word}\b", folded):
            return COLOR_WORDS[word]
    return None


def fold_letter_size(s: str | None) -> str | None:
    if not s:
        return None
    folded = fold_text(s)
    for word in _LETTER_SIZE_WORDS_LONGEST_FIRST:
        if re.search(rf"\b{word}\b", folded):
            return LETTER_SIZES[word]
    return None


def fold_compound_size(s: str | None) -> str | None:
    """A catalog "size" column can hold two dimensions joined by 'x': a
    reducing bushing's two ends ("3/4x1/2", kept as a compound key so the
    query side can match "3/4 x 1/2" as one unit), or a square item's two
    equal sides ("4x4", collapsed to the single dimension "4" since
    customers say "4 square box", never "4x4 box").
    """
    if not s:
        return None
    folded = fold_text(s)
    if "x" in folded:
        parts = [p.strip() for p in folded.split("x")]
        if len(parts) == 2:
            a, b = fold_size(parts[0]), fold_size(parts[1])
            if a and b:
                return a if a == b else f"{a}x{b}"
    return fold_size(s)


def fold_size_or_variant(s: str | None) -> str | None:
    """Numeric/compound size first, then letter size (S/M/L/XL), then
    color -- in that priority, since a token can't be more than one."""
    return fold_compound_size(s) or fold_letter_size(s) or fold_color(s)


_POLE_RE = re.compile(
    r"(\d+)\s*-?\s*pole|\b(single)\s*-?\s*pole|\b(double|two)\s*-?\s*pole|\b(\d+)p\b",
    re.IGNORECASE,
)
_POLE_WORD_TO_N = {"single": "1", "double": "2", "two": "2"}


def fold_pole(s: str | None) -> str | None:
    """Circuit breaker pole count ("1P"/"2P") -- the catalog's size column
    only holds amperage ("20A"), pole count lives only in the description
    ("...20A 1-Pole..."), so this needs its own extraction, folded the same
    way on both the catalog-description side and the query side."""
    if not s:
        return None
    folded = fold_text(s)
    m = _POLE_RE.search(folded)
    if not m:
        return None
    n = m.group(1) or _POLE_WORD_TO_N.get((m.group(2) or m.group(3) or "").lower()) or m.group(4)
    return f"{n}P" if n else None


UNIT_SYNONYMS = {
    "each": "EA",
    "ea": "EA",
    "boxes": "BX",
    "box": "BX",
    "bx": "BX",
    "reels": "RL",
    "reel": "RL",
    "rl": "RL",
    "spool": "RL",
    "spools": "RL",
    "feet": "FT",
    "foot": "FT",
    "ft": "FT",
    "pairs": "PR",
    "pair": "PR",
    "pr": "PR",
    "dozen": "DZ",
    "dz": "DZ",
    "bags": "BG",
    "bag": "BG",
    "bg": "BG",
    "cases": "CS",
    "case": "CS",
    "cs": "CS",
    "meters": "M",
    "meter": "M",
    "metres": "M",
    "metre": "M",
    "m": "M",
}

VAGUE_UNIT = "vague"

VAGUE_QUANTIFIER_PHRASES = (
    "a couple of",
    "a couple",
    "couple of",
    "couple",
    "a few",
    "few",
    "several",
    "some",
)

_UNIT_PHRASES_LONGEST_FIRST = sorted(UNIT_SYNONYMS.keys(), key=len, reverse=True)


def fold_unit_word(s: str | None) -> str | None:
    """Free-text unit/quantity word -> catalog uom code, VAGUE_UNIT, or None."""
    if not s:
        return None
    folded = fold_text(s)
    for phrase in VAGUE_QUANTIFIER_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", folded):
            return VAGUE_UNIT
    for phrase in _UNIT_PHRASES_LONGEST_FIRST:
        if re.search(rf"\b{re.escape(phrase)}\b", folded):
            return UNIT_SYNONYMS[phrase]
    return None


# phrase -> sku family prefix. Longest phrase wins so "hex cap screw" beats "hex".
FAMILY_KEYWORDS = {
    "90 elbow": "EL90",
    "90 ell": "EL90",
    "elbow": "EL90",
    "ell": "EL90",
    # bare "90"/"90s" as industry shorthand for a 90-degree elbow
    # (E003-1: "50 of the 1/2″ black iron 90s", no "elbow"/"ell" word at all)
    "90": "EL90",
    "45 elbow": "EL45",
    "45 ell": "EL45",
    "tee": "TEE",
    "coupling": "CPL",
    "cplg": "CPL",
    "union": "UNION",
    "close nipple": "NIP",
    "nipple": "NIP",
    "cap": "CAP",
    "bushing": "BUSH",
    "bush": "BUSH",
    "ball valve": "BV",
    "gate valve": "GV",
    "hex cap screw": "HHCS",
    "hex cap": "HHCS",
    "hhcs": "HHCS",
    "hcs": "HHCS",
    "hex bolt": "HB",
    "hex bolts": "HB",
    "hex nut": "HN",
    "hex nuts": "HN",
    "nylock": "NL",
    "flat washer": "FW",
    "flat washers": "FW",
    "lock washer": "LW",
    "lock washers": "LW",
    "hose": "HOSE",
    "barb": "HC",
    "emt": "EMT",
    "conduit": "EMT",
    "breaker": "CB",
    "thhn": "THHN",
    "wire nut": "WN",
    "wire nuts": "WN",
    "glove": "GLV",
    "gloves": "GLV",
    "safety glass": "SG",
    "safety glasses": "SG",
    "duct tape": "TAPE",
    "tape": "TAPE",
    "padlock": "PADLOCK",
    "hose clamp": "CLAMP",
    "clamp": "CLAMP",
    "sq box": "BOX",
    "square box": "BOX",
    "blank cover": "COVER",
    "cover": "COVER",
    # EMT accessories are their own sku families (CONN-EMT-*, CPL-EMT-*,
    # STRAP-EMT-*) distinct from the EMT conduit stick itself -- must be
    # tried before the bare "emt"/generic "coupling" keywords below.
    "set screw connector": "CONN",
    "set screw coupling": "CPL",
    "one hole strap": "STRAP",
    "hard hat": "HH",
    "shackle": "SHACKLE",
    "anchor shackle": "SHACKLE",
}

_FAMILY_PHRASES_LONGEST_FIRST = sorted(FAMILY_KEYWORDS.keys(), key=len, reverse=True)


def fold_family(s: str | None) -> str | None:
    """Free-text description -> sku family prefix, or None if no keyword found."""
    if not s:
        return None
    folded = fold_text(s)
    for phrase in _FAMILY_PHRASES_LONGEST_FIRST:
        # Plurals are formed by adding (e)s to the last word of the phrase
        # ("elbow" -> "elbows", "ball valve" -> "ball valves") -- allow it.
        if re.search(rf"\b{re.escape(phrase)}(?:e?s)?\b", folded):
            return FAMILY_KEYWORDS[phrase]
    return None


def canonical_family_keywords() -> dict[str, str]:
    return dict(FAMILY_KEYWORDS)
