"""argparse wiring for `python -m rfq {extract,match,run}`.

Flags are a fixed contract -- no hidden defaults, no extra required
flags, since these commands are run unchanged on unseen email sets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rfq import catalog as catalogmod
from rfq import emails as emailsmod
from rfq import extract as extractmod
from rfq import matcher as matchermod

# data/ ships alongside rfq/ in the submission; resolved relative to this
# file, not the current working directory, so the two commands work
# regardless of where they're invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _REPO_ROOT / "data" / "catalog.csv"
_XREF_PATH = _REPO_ROOT / "data" / "xref.csv"


def _load_and_classify_batch(emails_dir: Path) -> dict[str, emailsmod.Email]:
    batch: dict[str, emailsmod.Email] = {}
    for p in sorted(emails_dir.glob("*.eml")):
        e = emailsmod.parse_email(p)
        emailsmod.classify_one(e)
        batch[e.id] = e
    emailsmod.classify_batch(batch)
    return batch


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_extract(args: argparse.Namespace) -> int:
    batch = _load_and_classify_batch(args.emails)
    lines_by_id = extractmod.extract_batch(batch)
    _write_json(args.out, lines_by_id)
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    # lines.json may be ANY conforming file, not necessarily ours -- no
    # sender/domain context exists in this shape, so customer:<domain>
    # xref entries never fire here (competitor/legacy still do). See
    # DECISIONS.md for this disclosed limitation; `run` below is the one
    # path that has real Email context to pass through instead.
    lines_by_id = json.loads(args.lines.read_text(encoding="utf-8"))
    catalog = catalogmod.load(_CATALOG_PATH, _XREF_PATH)
    predictions = matchermod.match_lines(lines_by_id, catalog)
    _write_json(args.out, predictions)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    batch = _load_and_classify_batch(args.emails)
    lines_by_id = extractmod.extract_batch(batch)
    catalog = catalogmod.load(_CATALOG_PATH, _XREF_PATH)

    sender_domains = {
        eid: (e.forward_origin.sender_domain if e.forward_origin else e.sender_domain)
        for eid, e in batch.items()
    }
    supersedes = {eid: emailsmod.find_supersedes(e, batch) for eid, e in batch.items()}

    predictions = matchermod.match_lines(lines_by_id, catalog, sender_domains, supersedes)
    _write_json(args.out, predictions)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rfq")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="email dir -> lines.json")
    extract_parser.add_argument("--emails", type=Path, required=True)
    extract_parser.add_argument("--out", type=Path, required=True)
    extract_parser.set_defaults(func=cmd_extract)

    match_parser = subparsers.add_parser("match", help="lines.json -> predictions.json")
    match_parser.add_argument("--lines", type=Path, required=True)
    match_parser.add_argument("--out", type=Path, required=True)
    match_parser.set_defaults(func=cmd_match)

    run_parser = subparsers.add_parser("run", help="email dir -> predictions.json (extract -> match)")
    run_parser.add_argument("--emails", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
