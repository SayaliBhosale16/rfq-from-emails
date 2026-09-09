#!/usr/bin/env python3
"""Validate the SHAPE of your output files.  This checks structure only — it scores nothing.

    python check_format.py --lines lines.json
    python check_format.py --pred predictions.json
    python check_format.py --lines lines.json --pred predictions.json

lines.json        one JSON object: email id -> list of verbatim line strings
predictions.json  one JSON object: email id -> {"supersedes": <id or null>, "line_items": [...]}

Every problem is printed as <email>.<line>.<field>; exit 0 = ok, 2 = shape problems,
1 = file missing or not JSON.  See CLAUDE.md for what each field means.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ABSTAINS = ("ambiguous", "not_in_catalog", "discontinued")


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_lines(data) -> list[str]:
    if not isinstance(data, dict):
        return ['lines.json must be a JSON object keyed by email id (e.g. "E001")']
    errs = []
    for eid, v in data.items():
        if not isinstance(v, list):
            errs.append(f"{eid}: must be a list of strings ([] when the email has no lines)")
            continue
        errs += [f"{eid}[{i}]: must be a string" for i, s in enumerate(v) if not isinstance(s, str)]
    return errs


def check_line_item(w: str, ln) -> list[str]:
    if not isinstance(ln, dict):
        return [f"{w}: must be an object"]
    errs = []
    if not isinstance(ln.get("raw"), str) or not ln["raw"].strip():
        errs.append(f"{w}.raw: required, the non-empty input string this line came from")
    if "qty" not in ln:
        errs.append(f"{w}.qty: required (a number, or null when unstated/conflicting)")
    elif ln["qty"] is not None and not _is_num(ln["qty"]):
        errs.append(f"{w}.qty: must be a number or null")
    sku, ab = ln.get("sku"), ln.get("abstain")
    if (sku is None) == (ab is None):
        errs.append(f"{w}.sku: exactly one of 'sku' or 'abstain' must be set")
    if sku is not None and not isinstance(sku, str):
        errs.append(f"{w}.sku: must be a string")
    if ab is not None and ab not in ABSTAINS:
        errs.append(f"{w}.abstain: must be one of {list(ABSTAINS)}")
    if not isinstance(ln.get("why"), str) or not ln["why"].strip():
        errs.append(f"{w}.why: required, one short line of checkable evidence")
    cands = ln.get("candidates")
    if cands is not None and (
        not isinstance(cands, list) or not all(isinstance(c, str) for c in cands)
    ):
        errs.append(f"{w}.candidates: must be a list of sku strings")
    if ab == "ambiguous" and not (isinstance(cands, list) and len(cands) >= 2):
        errs.append(f"{w}.candidates: 'abstain': 'ambiguous' requires at least 2 candidate skus")
    if ln.get("confidence") is not None and not _is_num(ln["confidence"]):
        errs.append(f"{w}.confidence: must be a number (it is optional)")
    if ln.get("uom") is not None and not isinstance(ln["uom"], str):
        errs.append(f"{w}.uom: must be a string (it is optional)")
    return errs


def check_pred(data) -> list[str]:
    if not isinstance(data, dict):
        return ['predictions.json must be a JSON object keyed by email id (e.g. "E001")']
    errs = []
    for eid, e in data.items():
        if not isinstance(e, dict):
            errs.append(f"{eid}: must be an object")
            continue
        sup = e.get("supersedes")
        if sup is not None and not isinstance(sup, str):
            errs.append(f"{eid}.supersedes: must be an email id string or null")
        lines = e.get("line_items")
        if not isinstance(lines, list):
            errs.append(f"{eid}.line_items: required, a list ([] when the email has no lines)")
            continue
        for i, ln in enumerate(lines):
            errs += check_line_item(f"{eid}.line_items[{i}]", ln)
    return errs


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lines", type=Path, help="lines.json to check")
    ap.add_argument("--pred", type=Path, help="predictions.json to check")
    a = ap.parse_args(argv)
    if a.lines is None and a.pred is None:
        ap.error("give --lines and/or --pred")
    errs = []
    for path, checker in ((a.lines, check_lines), (a.pred, check_pred)):
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"{path}: {e}", file=sys.stderr)
            return 1
        errs += [f"{path.name}: {e}" for e in checker(data)]
    for e in errs:
        print(e)
    if errs:
        return 2
    print("shape ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
