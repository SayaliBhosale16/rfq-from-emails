#!/usr/bin/env python3
"""Score an extractor's lines.json against ground truth.

    python tools/score_lines.py --pred out/lines.json --key data/dev/lines.json [--detail]

Headline numbers: missed lines and hallucinated lines
(quoted-thread/signature/noise leakage), then line recall and precision.
Pure function of the two JSON dicts -- works on any conforming lines.json
regardless of how it was produced.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path


def _align(pred_lines: list[str], key_lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Returns (matched, missed, hallucinated). Exact-string match first
    (order-independent within the email), then difflib over the leftovers
    to find near-misses worth showing in --detail without over-crediting a
    true miss as "just a reorder".
    """
    pred_remaining = list(pred_lines)
    key_remaining = list(key_lines)
    matched = []
    for line in list(pred_remaining):
        if line in key_remaining:
            matched.append(line)
            pred_remaining.remove(line)
            key_remaining.remove(line)
    return matched, key_remaining, pred_remaining


def score_lines(pred: dict, key: dict, detail: bool) -> int:
    all_ids = sorted(set(pred) | set(key), key=lambda x: (len(x), x))
    total_matched = total_missed = total_hallucinated = 0
    per_email_detail = []

    for eid in all_ids:
        pred_lines = pred.get(eid, [])
        key_lines = key.get(eid, [])
        matched, missed, hallucinated = _align(pred_lines, key_lines)
        total_matched += len(matched)
        total_missed += len(missed)
        total_hallucinated += len(hallucinated)
        if missed or hallucinated:
            per_email_detail.append((eid, missed, hallucinated))

    recall = total_matched / (total_matched + total_missed) if (total_matched + total_missed) else 1.0
    precision = (
        total_matched / (total_matched + total_hallucinated)
        if (total_matched + total_hallucinated)
        else 1.0
    )

    print(f"missed lines:       {total_missed}")
    print(f"hallucinated lines: {total_hallucinated}")
    print(f"line recall:        {recall:.3f}")
    print(f"line precision:     {precision:.3f}")

    if detail and per_email_detail:
        print()
        print("--- detail ---")
        for eid, missed, hallucinated in per_email_detail:
            print(f"{eid}:")
            for line in missed:
                print(f"  MISSED      : {line!r}")
                close = difflib.get_close_matches(line, pred.get(eid, []), n=1)
                if close:
                    print(f"    closest pred: {close[0]!r}")
                    print(
                        "    diff        : "
                        + " ".join(difflib.ndiff([line], close))
                    )
            for line in hallucinated:
                print(f"  HALLUCINATED: {line!r}")
                close = difflib.get_close_matches(line, key.get(eid, []), n=1)
                if close:
                    print(f"    closest key : {close[0]!r}")

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)

    pred = json.loads(args.pred.read_text(encoding="utf-8"))
    key = json.loads(args.key.read_text(encoding="utf-8"))
    return score_lines(pred, key, args.detail)


if __name__ == "__main__":
    sys.exit(main())
