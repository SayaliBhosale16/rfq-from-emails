import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EMAILS_DIR = REPO_ROOT / "data" / "dev" / "emails"
DEV_LINES = REPO_ROOT / "data" / "dev" / "lines.json"
CHECK_FORMAT = REPO_ROOT / "data" / "check_format.py"


def _run_module(args, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, "-m", "rfq", *args], cwd=cwd, capture_output=True, text=True
    )


def _check_format(*, lines=None, pred=None):
    args = []
    if lines is not None:
        args += ["--lines", str(lines)]
    if pred is not None:
        args += ["--pred", str(pred)]
    return subprocess.run(
        [sys.executable, str(CHECK_FORMAT), *args], capture_output=True, text=True
    )


def test_extract_produces_valid_shape(tmp_path):
    out = tmp_path / "lines.json"
    result = _run_module(["extract", "--emails", str(EMAILS_DIR), "--out", str(out)])
    assert result.returncode == 0, result.stderr
    check = _check_format(lines=out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_extract_covers_every_input_email(tmp_path):
    out = tmp_path / "lines.json"
    _run_module(["extract", "--emails", str(EMAILS_DIR), "--out", str(out)])
    data = json.loads(out.read_text())
    eml_ids = {p.stem for p in EMAILS_DIR.glob("*.eml")}
    assert set(data.keys()) == eml_ids


def test_match_produces_valid_shape(tmp_path):
    out = tmp_path / "predictions.json"
    result = _run_module(["match", "--lines", str(DEV_LINES), "--out", str(out)])
    assert result.returncode == 0, result.stderr
    check = _check_format(pred=out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_match_standalone_has_no_sender_domain_but_still_scores_well(tmp_path):
    """Cold match on a bare lines.json (no Email context) must still
    resolve the vast majority of dev lines correctly -- the disclosed
    customer:<domain> xref limitation affects only 2 of 86 lines
    (TSM64072, both instances), not the whole matcher.
    """
    out = tmp_path / "predictions.json"
    _run_module(["match", "--lines", str(DEV_LINES), "--out", str(out)])
    predictions = json.loads(out.read_text())
    key = json.loads((REPO_ROOT / "data" / "dev" / "answer_key.json").read_text())

    total = correct = 0
    for eid, edata in key["emails"].items():
        for li, pred in zip(edata["line_items"], predictions[eid]["line_items"]):
            total += 1
            if pred.get("sku") == li["sku"] and pred.get("abstain") == li["abstain"]:
                correct += 1
    assert correct / total >= 0.9


def test_run_produces_valid_shape(tmp_path):
    out = tmp_path / "predictions.json"
    result = _run_module(["run", "--emails", str(EMAILS_DIR), "--out", str(out)])
    assert result.returncode == 0, result.stderr
    check = _check_format(pred=out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_run_resolves_domain_scoped_xref_that_cold_match_cannot(tmp_path):
    """`run` has real Email context (sender domain), so it can resolve
    TSM64072 (customer:tristate-mechanical.com xref) where cold `match`
    on a bare lines.json structurally cannot -- the one concrete advantage
    of `run` over separate extract+match.
    """
    out = tmp_path / "predictions.json"
    _run_module(["run", "--emails", str(EMAILS_DIR), "--out", str(out)])
    predictions = json.loads(out.read_text())
    e003_items = {li["raw"]: li for li in predictions["E003"]["line_items"]}
    assert e003_items["TSM64072 x 40"]["sku"] == "NIP-1/2-CL-BI"
