#!/usr/bin/env python3
"""Score a matcher's predictions.json against the answer key.

    python tools/score_match.py --pred out/predictions.json --key data/dev/answer_key.json [--detail]

Headline numbers: confident wrong matches (named a sku, the right answer
was a different sku or an abstention) and spurious abstentions (abstained
where the key matched). Then match accuracy, qty accuracy (respecting the
key's qty_tol; null must equal null exactly), and abstain precision/recall.

Aligns by RAW STRING within each email, not list position -- this is what
lets it run against ANY conforming predictions.json regardless of what
produced the underlying lines.json. A key line with no matching raw in
pred is reported separately as "uncovered" (an extraction-coverage gap,
not a matching error) and kept out of the headline counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _qty_ok(pred_qty, key_qty, qty_tol) -> bool:
    if key_qty is None:
        return pred_qty is None
    if not _is_num(pred_qty):
        return False
    if qty_tol is None:
        return True  # unscored per the key
    if qty_tol == 0:
        return pred_qty == key_qty
    return abs(pred_qty - key_qty) <= qty_tol * key_qty


def score_match(pred: dict, key: dict, detail: bool) -> int:
    key_emails = key.get("emails", key)  # tolerate a bare {email: {...}} shape too

    confident_wrong = []
    spurious_abstentions = []
    uncovered = []
    match_total = match_correct = 0
    qty_total = qty_correct = 0
    abstain_pred_count = abstain_key_count = abstain_both_count = 0

    for eid, key_email in key_emails.items():
        pred_email = pred.get(eid, {})
        pred_lines = {li.get("raw"): li for li in pred_email.get("line_items", [])}

        for key_line in key_email.get("line_items", []):
            raw = key_line["raw"]
            pred_line = pred_lines.get(raw)
            if pred_line is None:
                uncovered.append((eid, raw))
                continue

            key_sku = key_line.get("sku")
            key_abstain = key_line.get("abstain")
            pred_sku = pred_line.get("sku")
            pred_abstain = pred_line.get("abstain")

            match_total += 1
            if pred_sku is not None and key_sku is not None and pred_sku == key_sku:
                match_correct += 1
            elif pred_sku is not None:
                # named a sku; key either wanted a different sku or an abstention
                confident_wrong.append((eid, raw, pred_sku, key_sku or key_abstain))
            elif pred_abstain is not None and key_sku is not None:
                spurious_abstentions.append((eid, raw, pred_abstain, key_sku))
            elif pred_abstain is not None and key_abstain is not None:
                match_correct += 1  # correct abstention counts as a correct match

            if key_abstain is not None:
                abstain_key_count += 1
            if pred_abstain is not None:
                abstain_pred_count += 1
            if key_abstain is not None and pred_abstain is not None:
                abstain_both_count += 1

            if pred_sku is not None and key_sku is not None and pred_sku == key_sku:
                qty_total += 1
                if _qty_ok(pred_line.get("qty"), key_line.get("qty"), key_line.get("qty_tol")):
                    qty_correct += 1

    match_accuracy = match_correct / match_total if match_total else 1.0
    qty_accuracy = qty_correct / qty_total if qty_total else 1.0
    abstain_precision = abstain_both_count / abstain_pred_count if abstain_pred_count else 1.0
    abstain_recall = abstain_both_count / abstain_key_count if abstain_key_count else 1.0

    print(f"confident wrong matches: {len(confident_wrong)}")
    print(f"spurious abstentions:    {len(spurious_abstentions)}")
    print(f"uncovered key lines:     {len(uncovered)}  (extraction-coverage gap, not scored above)")
    print(f"match accuracy:          {match_accuracy:.3f}  ({match_correct}/{match_total})")
    print(f"qty accuracy:            {qty_accuracy:.3f}  ({qty_correct}/{qty_total}, matched skus only)")
    print(f"abstain precision:       {abstain_precision:.3f}")
    print(f"abstain recall:          {abstain_recall:.3f}")

    if detail:
        print()
        print("--- detail ---")
        for eid, raw, pred_sku, key_answer in confident_wrong:
            print(f"CONFIDENT WRONG  {eid}: {raw!r}")
            print(f"    pred sku={pred_sku!r}  key wanted={key_answer!r}")
        for eid, raw, pred_abstain, key_sku in spurious_abstentions:
            print(f"SPURIOUS ABSTAIN {eid}: {raw!r}")
            print(f"    pred abstain={pred_abstain!r}  key sku={key_sku!r}")
        for eid, raw in uncovered:
            print(f"UNCOVERED        {eid}: {raw!r}")

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)

    pred = json.loads(args.pred.read_text(encoding="utf-8"))
    key = json.loads(args.key.read_text(encoding="utf-8"))
    return score_match(pred, key, args.detail)


if __name__ == "__main__":
    sys.exit(main())
