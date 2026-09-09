#!/usr/bin/env python3
"""Generalization probe. Run before declaring the matcher done, not as a
final rubber-stamp.

Two families of probe, asking different questions:

FORM-PRESERVING mutations rewrite a line without changing what it means
(whitespace, unicode fractions, dash style, clause order, case). The
answer must not move at all. A drift here is a bug -- it means
parse.py/resolve.py depends on a literal character or on source casing
instead of routing through normalize.py's folding.

ABLATIONS delete real information (the material/finish word, the size).
The answer SHOULD move -- the interesting question is which direction.
Less evidence must produce less commitment: an abstention, never a
different confidently-named sku. This is the direct test of the cost
asymmetry the README grades on ("a wrong match becomes a wrong quote a
customer receives... an abstention becomes a slower quote an ISR
reviews"), and of the claim that material is a hard constraint.

Outcomes are classified, not just counted, because "0 broke" cannot
distinguish failing safely from failing dangerously:

    held           same sku as before        (form-preserving: pass)
    degraded_safe  -> an abstention          (ablation: pass; form: minor)
    held_unique    same sku, but only one row ever existed for that
                   family+size, so the deleted attribute was not what
                   decided it -- correct, not a guess
    held_guessed   same sku while several rows existed -- REVIEW: either a
                   real guess, or residual evidence still in the line
    flipped_wrong  -> a DIFFERENT sku        (always a real failure)

    python tools/mutate.py [--lines data/dev/answer_key.json] [--detail]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rfq import catalog as catmod, normalize
from rfq.parse import parse_line
from rfq.quantity import resolve_quantity
from rfq.resolve import resolve

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ASCII_FRACTIONS = {
    "¼": "1/4", "½": "1/2", "¾": "3/4",
    "⅓": "1/3", "⅔": "2/3",
    "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}
_FRACTION_TABLE = str.maketrans(ASCII_FRACTIONS)


# --------------------------------------------------------------------------
# Form-preserving mutations: meaning unchanged, answer must not move.
# --------------------------------------------------------------------------


def _reflow_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _unicode_fraction_to_ascii(s: str) -> str:
    return s.translate(_FRACTION_TABLE)


def _swap_dash_style(s: str) -> str:
    if " - " in s:
        return s.replace(" - ", " – ")
    if " – " in s:
        return s.replace(" – ", " - ")
    return s


def _reorder_clause_punctuation(s: str) -> str:
    # "10 of the 3/4 tees, black iron" -> "10 of the 3/4 tees black iron,"
    # (only when there's exactly one comma-separated trailing pair, so this
    # doesn't scramble multi-clause lines into something meaningless).
    if s.count(",") != 1:
        return s
    before, after = s.split(",", 1)
    after = after.strip()
    if not after or "," in after:
        return s
    return f"{before} {after},"


def _case_swap(s: str) -> str:
    return s.upper() if s != s.upper() else s.lower()


MUTATIONS = {
    "reflow_whitespace": _reflow_whitespace,
    "unicode_fraction_to_ascii": _unicode_fraction_to_ascii,
    "swap_dash_style": _swap_dash_style,
    "reorder_clause_punctuation": _reorder_clause_punctuation,
    "case_swap": _case_swap,
}


# --------------------------------------------------------------------------
# Ablations: delete information, then check the failure direction.
# --------------------------------------------------------------------------

# longest phrase first so "black iron" is removed whole rather than
# leaving a stray "iron" behind
_MATERIAL_PHRASES = sorted(normalize.MATERIAL_SYNONYMS.keys(), key=len, reverse=True)
# IGNORECASE matters for the metric form: "M12" is a size, and without it
# only a lowercase "m12" would be removed.
_SIZE_TOKEN_RE = re.compile(r"\d+-\d+/\d+|\d+/\d+|\bm\d+\b", re.IGNORECASE)
# A leading quantity is a whole number NOT followed by "/" -- otherwise the
# "3" of a leading "3/4" gets stripped as a quantity and the fraction is
# left as a meaningless "/4".
_LEADING_QTY_RE = re.compile(r"^\s*\d+(?!\s*/)\s*")


def _drop_material(s: str) -> str:
    """Delete the finish/material word ("zinc", "brass", "black iron", "SS").
    A matcher that still names one specific sku afterwards, where several
    materials existed, has guessed one the customer never stated.
    """
    out = s
    for phrase in _MATERIAL_PHRASES:
        out = re.sub(rf"\b{re.escape(phrase)}\b", " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip(" ,-–—")


def _drop_size(s: str) -> str:
    """Delete fraction/metric size tokens, keeping the leading quantity.

    Deliberately does NOT strip bare decimals: in this catalog "8.8" and
    "A2" are metric material classes, not sizes, so removing them would
    silently turn this into a second (and mislabelled) material ablation.
    """
    m = _LEADING_QTY_RE.match(s)
    head, tail = (m.group(0), s[m.end():]) if m else ("", s)
    tail = _SIZE_TOKEN_RE.sub(" ", tail)
    return (head + re.sub(r"\s{2,}", " ", tail)).strip(" ,-–—")


ABLATIONS = {
    "drop_material": _drop_material,
    "drop_size": _drop_size,
}

HELD = "held"
DEGRADED_SAFE = "degraded_safe"
# An ablation that still yields one sku splits two ways, and the
# difference matters: if the catalog only ever had one row for that
# family+size, deleting the material removed nothing the filter was
# using, and holding is correct (gate valves are brass-only, caps are
# black-iron-only). If several materials did exist and we still named
# one, we guessed an attribute the customer never stated -- the actual
# failure this ablation is hunting for.
HELD_UNIQUE = "held_unique"
HELD_GUESSED = "held_guessed"
FLIPPED_WRONG = "flipped_wrong"


def _resolve_line(raw: str, catalog) -> tuple[str | None, str | None, float | None]:
    query = parse_line(raw)
    resolution = resolve(query, catalog, sender_domain=None)
    qty = None
    if resolution.sku is not None:
        row = catalog.by_sku[resolution.sku.upper()]
        qty, _ = resolve_quantity(query, row)
    return resolution.sku, resolution.abstain, qty


def _resolve_why(raw: str, catalog) -> str:
    return resolve(parse_line(raw), catalog, sender_domain=None).why


def _peer_row_count(catalog, sku: str) -> int:
    """How many active rows share this row's family and size -- i.e. how
    many the material/finish constraint was actually choosing between.
    """
    row = catalog.by_sku[sku.upper()]
    return sum(
        1
        for r in catalog.by_family.get(row.family, [])
        if r.status == "active" and r.size_key == row.size_key
    )


def _classify(gold_sku: str, mutated_sku: str | None, is_ablation: bool, catalog) -> str:
    if mutated_sku is None:
        return DEGRADED_SAFE
    if mutated_sku == gold_sku:
        if not is_ablation:
            return HELD
        return HELD_UNIQUE if _peer_row_count(catalog, gold_sku) == 1 else HELD_GUESSED
    return FLIPPED_WRONG


def run_probe(answer_key: dict, catalog, detail: bool) -> dict:
    probes = [(n, f, False) for n, f in MUTATIONS.items()]
    probes += [(n, f, True) for n, f in ABLATIONS.items()]
    results: dict[str, dict[str, list]] = {
        name: {HELD: [], DEGRADED_SAFE: [], HELD_UNIQUE: [], HELD_GUESSED: [], FLIPPED_WRONG: []}
        for name, _, _ in probes
    }
    total_checked = 0

    for edata in answer_key["emails"].values():
        for line in edata["line_items"]:
            raw, gold_sku = line["raw"], line["sku"]
            if gold_sku is None:
                continue  # only probe lines with a resolvable gold sku
            original_sku, _, _ = _resolve_line(raw, catalog)
            if original_sku != gold_sku:
                continue  # already wrong before mutation; not this probe's job
            total_checked += 1

            # An ablation can only test the attribute filter. A line that
            # resolves by identifier short-circuits that filter entirely
            # (E002-2's competitor number), so deleting its material proves
            # nothing -- counting it as a "guess" would be a false positive.
            resolves_by_identifier = "identifier" in _resolve_why(raw, catalog)

            for name, fn, is_ablation in probes:
                mutated = fn(raw)
                if not mutated or mutated == raw:
                    continue
                if is_ablation:
                    if resolves_by_identifier:
                        continue
                    # Guard against an ablation that only reflowed
                    # whitespace: if the folded forms match, no information
                    # was actually deleted and there is nothing to test.
                    if normalize.fold_text(mutated) == normalize.fold_text(raw):
                        continue
                mutated_sku, mutated_abstain, _ = _resolve_line(mutated, catalog)
                outcome = _classify(gold_sku, mutated_sku, is_ablation, catalog)
                results[name][outcome].append(
                    (line["id"], raw, mutated, mutated_sku, mutated_abstain)
                )

    print(f"lines probed (dev lines the matcher already gets right): {total_checked}")
    print()
    print("form-preserving (meaning unchanged -- the answer must not move):")
    print(f"  {'mutation':<28}{'applied':>8}{'held':>7}{'->abstain':>11}{'->WRONG SKU':>13}")
    for name in MUTATIONS:
        r = results[name]
        applied = sum(len(v) for v in r.values())
        print(
            f"  {name:<28}{applied:>8}{len(r[HELD]):>7}"
            f"{len(r[DEGRADED_SAFE]):>11}{len(r[FLIPPED_WRONG]):>13}"
        )
    print()
    print("ablation (information deleted -- must degrade, safely):")
    print(f"  {'ablation':<24}{'applied':>8}{'->abstain':>11}{'held(unique)':>14}{'held(GUESS)':>13}{'->WRONG SKU':>13}")
    for name in ABLATIONS:
        r = results[name]
        applied = sum(len(v) for v in r.values())
        print(
            f"  {name:<24}{applied:>8}{len(r[DEGRADED_SAFE]):>11}"
            f"{len(r[HELD_UNIQUE]):>14}{len(r[HELD_GUESSED]):>13}{len(r[FLIPPED_WRONG]):>13}"
        )

    dangerous = sum(len(r[FLIPPED_WRONG]) for r in results.values())
    guessed = sum(len(results[n][HELD_GUESSED]) for n in ABLATIONS)
    print()
    print(f"HEADLINE -- rewrites producing a DIFFERENT confident sku: {dangerous}")
    print(f"           ablations holding a sku for review (held(GUESS)): {guessed}")
    print("(the first becomes a wrong quote at the receiving dock; everything")
    print(" else degrades to an abstention an ISR reviews. held(GUESS) is a")
    print(" review queue, not a verdict -- residual evidence may still")
    print(" identify the row after the attribute word is deleted.)")

    if detail:
        print()
        print("--- detail ---")
        for name in results:
            for outcome in (FLIPPED_WRONG, HELD_GUESSED, HELD_UNIQUE, DEGRADED_SAFE):
                for line_id, raw, mutated, sku, abstain in results[name][outcome]:
                    print(f"{outcome:<15} {name:<28} {line_id}")
                    print(f"    original: {raw!r}")
                    print(f"    mutated:  {mutated!r}")
                    print(f"    result:   sku={sku!r} abstain={abstain!r}")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lines", type=Path, default=DATA_DIR / "dev" / "answer_key.json")
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)

    answer_key = json.loads(args.lines.read_text(encoding="utf-8"))
    catalog = catmod.load(DATA_DIR / "catalog.csv", DATA_DIR / "xref.csv")
    run_probe(answer_key, catalog, args.detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
