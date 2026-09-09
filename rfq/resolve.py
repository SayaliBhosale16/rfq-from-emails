"""Query -> filtered catalog rows -> sku / ambiguous / not_in_catalog / discontinued.

- Those fields become filters over the catalog. 
- One row survives -> that's the SKU | Zero or several survive -> it abstains.

Constraint filter over catalog.csv's attribute grid -- never
similarity/embeddings/fuzzy matching. Resolution order is fixed:
identifier lookup first, then attribute filtering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rfq import normalize
from rfq.catalog import Catalog, CatalogRow, family_candidates, lookup_identifier
from rfq.parse import Query

# uoms that require the customer to explicitly NAME the pack (README: "the
# pack sku is right only when the customer names the pack (reel, dozen,
# box)") -- when a family has both a pack-uom row and a non-pack row for
# the same size/material, the pack row should only survive if the stated
# unit word actually names it.
_PACK_UOMS = {"BX", "RL", "DZ", "CS"}

_HINT_STOPWORDS = {
    "the", "of", "a", "an", "and", "or", "for", "to", "with", "in", "on", "at",
    "that", "is", "please", "quote", "send", "need", "add", "get", "also",
    "can", "you", "we", "this", "it", "from", "as", "by", "if", "are", "have",
    "has", "will", "would", "could", "should", "make", "not", "rest", "stays",
    "same", "x",
}
_HINT_WORD_RE = re.compile(r"[a-z]+")


@dataclass
class Resolution:
    sku: str | None
    abstain: str | None  # "ambiguous" | "not_in_catalog" | "discontinued" | None
    candidates: list[str] = field(default_factory=list)
    why: str = ""


def _matches_named_option(row: CatalogRow, option: dict) -> bool:
    size = option.get("size")
    return size is None or row.size_key == size


def _description_hints(folded_text: str) -> list[str]:
    return [w for w in _HINT_WORD_RE.findall(folded_text) if len(w) >= 3 and w not in _HINT_STOPWORDS]


def _narrow_by_description_hints(rows: list[CatalogRow], hints: list[str]) -> list[CatalogRow]:
    """A pure disambiguation aid for rows structured columns can't tell
    apart (E006-1: "push-lok hose" vs "air hose" are both HOSE-3/8-* rows
    with the same size, differing only in description text) -- only ever
    NARROWS when it strictly helps, never used to reject a whole candidate
    set to zero.
    """
    if not hints or len(rows) <= 1:
        return rows
    scored = [(sum(1 for h in hints if h in row.description.lower()), row) for row in rows]
    best = max(score for score, _ in scored)
    if best == 0:
        return rows
    return [row for score, row in scored if score == best]


def _narrow_by_unit_word(rows: list[CatalogRow], unit_word: str | None) -> list[CatalogRow]:
    """A unit word is evidence; its absence is not license to guess between
    a base-unit row and a pack row that both fit (README) -- but it's also
    not license to prefer a PACK row nobody named. When the surviving rows
    span more than one uom, prefer the one the customer actually named; if
    they named none, prefer the non-pack default(s), never eliminate the
    only rows down to zero.
    """
    if len(rows) <= 1:
        return rows
    uoms = {r.uom for r in rows}
    if len(uoms) <= 1:
        return rows
    if unit_word and unit_word in uoms:
        return [r for r in rows if r.uom == unit_word]
    non_pack = [r for r in rows if r.uom not in _PACK_UOMS]
    return non_pack if non_pack else rows


def resolve(query: Query, catalog: Catalog, sender_domain: str | None = None) -> Resolution:
    # 1. Identifier lookup first -- exact match against our own sku column
    # or a domain-scoped/unconditional xref entry. This is a SEPARATE code
    # path from attribute filtering, never merged with it: E009-4 types the
    # dead code "EL90-1/2-GALV" directly -> discontinued, offering only
    # replaced_by; the same physical part reached by plain description with
    # no material stated (E002-1) is one of 4 ambiguous candidates, and the
    # discontinued row never appears there at all.
    seen_tokens = set()
    for tok in [query.raw, *query.identifier_tokens]:
        if tok in seen_tokens:
            continue
        seen_tokens.add(tok)
        row = lookup_identifier(catalog, tok, sender_domain)
        if row is None:
            continue
        if row.status == "discontinued":
            return Resolution(
                sku=None,
                abstain="discontinued",
                candidates=[row.replaced_by] if row.replaced_by else [],
                why=f"identifier resolves to discontinued {row.sku}; replaced by {row.replaced_by}",
            )
        return Resolution(sku=row.sku, abstain=None, candidates=[], why="identifier/xref exact match")

    # 2. Attribute filter.
    if query.family:
        candidates = family_candidates(catalog, query.family)
    else:
        candidates = [r for r in catalog.rows if r.status == "active"]

    deciding: list[str] = []

    if query.material == normalize.MATERIAL_NAMED_BUT_UNKNOWN:
        phrase = normalize.describe_unknown_material(normalize.fold_text(query.raw))
        return Resolution(
            sku=None,
            abstain="not_in_catalog",
            candidates=[],
            why=f"no {phrase or 'matching material'} in catalog",
        )

    # named_options REPLACES the size/length filter below (candidates are
    # exactly the named options, not the whole family -- E002-4: "1/2 or
    # 3/4" -> only those two sizes), but every OTHER stated attribute
    # (material, thread, ...) still applies as its own hard constraint on
    # top: "1/2 or 3/4 threaded" also means brass+FNPT, not every thread
    # type at those two sizes.
    if query.named_options:
        candidates = [
            r for r in candidates if any(_matches_named_option(r, opt) for opt in query.named_options)
        ]
        deciding.append("named size options")
    else:
        # A reducing bushing's two ends ("3/4 x 1/2") are stored as ONE
        # compound size column ("3/4x1/2") -- try that combined form before
        # falling back to separate size/length filters, and skip the
        # separate length filter entirely when it's what matched (the row
        # has no length column populated at all in that case).
        compound = f"{query.size}x{query.length}" if (query.size and query.length) else None
        compound_hit = compound and any(r.size_key == compound for r in candidates)

        if compound_hit:
            candidates = [r for r in candidates if r.size_key == compound]
            deciding.append("compound size")
        else:
            if query.size is not None:
                candidates = [r for r in candidates if r.size_key == query.size]
                deciding.append("size")
            if query.length is not None:
                candidates = [r for r in candidates if normalize.fold_size(r.length) == query.length]
                deciding.append("length")

    if query.material is not None:
        candidates = [r for r in candidates if r.material_key == query.material]
        deciding.append("material")
    if query.thread is not None:
        candidates = [
            r for r in candidates if (r.thread or "").upper() == str(query.thread).upper()
        ]
        deciding.append("thread")
    if query.pole is not None:
        candidates = [r for r in candidates if r.pole == query.pole]
        deciding.append("pole count")

    active = [r for r in candidates if r.status == "active"]

    if not active:
        return Resolution(
            sku=None,
            abstain="not_in_catalog",
            candidates=[],
            why=f"no {query.family or 'matching'} row for stated attributes",
        )

    # Unit-word/pack-uom preference before description-hint scoring: it's a
    # structured, reliable signal (the stated word directly names a row's
    # own uom) -- description-hint scoring is a fuzzy last resort and can
    # otherwise win on a coincidental word match before the reliable signal
    # gets a chance (E006-3: "large" appears in GLV-NIT-EXAM-L-BX's
    # description too, but "pairs" -> PR uniquely picks GLV-NIT-L first).
    if len(active) > 1:
        narrowed = _narrow_by_unit_word(active, query.unit_word)
        if len(narrowed) < len(active):
            active = narrowed
            deciding.append("stated unit")

    if len(active) > 1:
        hints = _description_hints(normalize.fold_text(query.raw))
        narrowed = _narrow_by_description_hints(active, hints)
        if len(narrowed) < len(active):
            active = narrowed
            deciding.append("description keyword")

    if len(active) == 1:
        row = active[0]
        why = (
            f"exact {'+'.join(deciding)} match; single candidate"
            if deciding
            else "single active candidate for family"
        )
        return Resolution(sku=row.sku, abstain=None, candidates=[], why=why)

    reason = (
        f"{len(active)} candidates fit; {', '.join(deciding)} not sufficient to disambiguate"
        if deciding
        else f"{len(active)} candidates fit; no distinguishing attribute stated"
    )
    return Resolution(
        sku=None, abstain="ambiguous", candidates=sorted(r.sku for r in active), why=reason
    )
