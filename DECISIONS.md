# DECISIONS

## Something in the data that surprised me

`catalog.csv`'s `size` column doesn't mean one thing — it means something
different per category, undocumented. Pipe fittings/valves: a fraction
(`1/2`). Gloves: a letter (`S`/`M`/`L`/`XL`), but customers write the
*word* ("large"). Wire nuts: a color (`Yellow`) stored in the size
column, while safety glasses/hard hats leave size empty and put the color
only in free text (`SG-SMK` = "Smoke Lens"). Circuit breakers: amperage
(`20A`), with pole count (`1-Pole`/`2-Pole`) living only in the
description. Reducing bushings: two dimensions joined with no space
(`3/4x1/2`). Square boxes: the same trick with equal values (`4x4` for
one 4" dimension). THHN wire: gauge is in `size` (`14 AWG`), but the
spool length sits in the description ("500 ft") reading exactly like a
plausible customer quantity. A smaller one: a `-SS` suffix means
"stainless" everywhere except the EMT connector/coupling family, where it
means "Set Screw" — a real naming collision, not a bug.

## A design tension I disagree with, and what I did about it

**"The matcher must accept any file in the lines.json format"** is in
real tension with **"cross-references... valid only for mail from that
domain"** — a bare `lines.json` line has no sender at all. I did not
special-case around this: `resolve()` takes an optional `sender_domain`;
`match` (which only ever sees `lines.json`) always passes `None`, so
`customer:<domain>` xref entries correctly never fire there — only
domain-unconditional `competitor:*`/`legacy` do. `run` (which has real
`Email` objects) threads the actual sender domain through instead.
Concretely, this is why E003-4/E010-4 (`TSM64072 x 40`, a customer number
with no other descriptive content) can't resolve under cold `match` but
does under `run` — verified: spurious abstentions drop from 4 to 2 when
the same example set runs through `run` instead of separate
`extract`+`match`. I consider this the interface working as specified,
not a bug — guessing a domain that isn't there would be exactly the
invented information the design rule forbids.

Smaller, against my own `CLAUDE.md` (the design spec in this repo): it
frames parentheticals as "kept whole even when long," a rule separate
from dash-clause dropping. But E006-1's drops (no dimension) while
E002-2's is kept (has one) — one dimension-content test governs both, and
the code implements that. E014-17's survives for an unrelated reason:
numbered list items are atomic, so nothing is trimmed.

## Generalization estimate on an unseen set, and the two kinds of line I expect to lose

Example set (86 lines, cold `match`, no domain): 0 confident wrong, 4
spurious abstentions, match accuracy 0.953, qty 0.987. Extraction 1
missed / 1 hallucinated, both the documented key transcription slip.

For an unseen set of ~30 emails at this density (4.8 lines/email, ~145
lines) I expect a real gap, because a system that scores near-perfect on
the set it was built against and asserts it will generalise has been
fitted, not measured:

| | estimate |
|---|---|
| missed lines | 6–12 (recall ~92–96%) |
| hallucinated lines | 2–6 (precision ~96–99%) |
| confident wrong matches | 2–5 (1.5–3.5%) |
| spurious abstentions | 10–18 (7–12%) |
| SKU accuracy | 84–90% |
| quantity accuracy | 88–94% |

I expect spurious abstentions to run ~4× confident wrongs, and that
ratio is measured rather than hoped for: `tools/mutate.py` deletes a
stated attribute from every example line it already gets right, and of 97
such ablations 86 degrade to an abstention, 10 hold a row that was
unique anyway, 1 is flagged for review, and **0 produce a different
confident SKU** — as do 0 of 136 form-preserving rewrites. The system is
built to fail toward the cheaper error; that is the evidence for it.

Both loss modes below land in spurious abstentions, not confident
wrongs, which is why the first column stays small:

1. **Prose phrasing the anchorer doesn't cover.** Its leading-connective
   policy and wrapper-stripping were built from four sentences (E003,
   E006, E010, E018). A wrapper verb outside my fixed list, or an
   elliptical continuation with no dimension-bearing head noun, either
   keeps commentary or loses a real item.
2. **A family/material synonym absent from `normalize.py`'s tables**,
   which were built from 18 emails' vocabulary, not a trade thesaurus. An
   unseen synonym ("close nip", a brand name) yields `family=None` and a
   whole-catalog scan — a spuriously large `ambiguous` list, or a wrong
   `not_in_catalog`.

## Design memo

Extraction tries structural readers first (csv/html/aligned-table/list)
and falls back to `prose_anchor` only when none recognise the shape. The
anchorer scans for quantity-shaped anchors rather than splitting on
commas, since a comma inside a description (E003-3: "3/4 tees, black
iron") isn't an item boundary.

Matching is a pure constraint filter, never similarity/embeddings.
Identifier lookup is a separate code path from attribute filtering, tried
first — that is what keeps a discontinued sku typed literally (E009-4)
out of the *ambiguous* candidates for the same part reached by
description (E002-1). Rejected: any fuzzy-string layer, and
auto-substituting a discontinued sku's replacement or the nearest
material — both are the invented-information failure this design guards
against.

Known-broken: the `FT`→sticks conversion has no example exercise (every
example EMT line states length via the sku shape), likewise the "base and
pack sku both exist, no unit word → ambiguous" rule. `supersedes` is
best-effort via `In-Reply-To` and unscored, so not over-invested.

Disclosed interface limit: E017-1's key wants `qty: null` because the
*subject* conflicts with the row's qty — but `match()` sees only the bare
string. I didn't special-case subject-sniffing; 25 is the only value
derivable from the input.

Agentic `run` (not built): a bounded loop — extract, catalog search, xref
lookup — one resolve attempt per line, then decide or abstain. Forbidden:
substituting a discontinued sku, guessing a material, inventing a domain.
Skipped because the stages are a fixed sequence and the stopping rule is
"one candidate or not" — an agent adds per-line cost and non-determinism
for flexibility this problem doesn't need.
