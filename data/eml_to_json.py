#!/usr/bin/env python3
"""Optional helper: .eml -> JSON.  Use it, port it, or ignore it.  Stdlib only.

    python eml_to_json.py dev/emails/E001.eml            # one email -> stdout
    python eml_to_json.py dev/emails [--out inbox.json]  # directory -> {id: email}

Shape: {id, headers{from,to,date,subject,message_id,in_reply_to,references},
        text, html, attachments:[{filename, mime, text}]}
"""

from __future__ import annotations

import argparse
import json
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

HEADERS = ("from", "to", "date", "subject", "message-id", "in-reply-to", "references")


def _text(part) -> str | None:
    if part.get_content_maintype() != "text":
        return None
    try:
        return part.get_content()
    except Exception:  # unknown charset / bad transfer encoding
        return part.get_payload(decode=True).decode("utf-8", "replace")


def eml_to_json(path: Path) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    out = {
        "id": path.stem,
        "headers": {h.replace("-", "_"): msg.get(h) for h in HEADERS},
        "text": None,
        "html": None,
        "attachments": [],
    }
    for part in msg.walk():
        if part.is_multipart():
            continue
        ct = part.get_content_type()
        if part.get_content_disposition() == "attachment" or part.get_filename():
            out["attachments"].append(
                {"filename": part.get_filename(), "mime": ct, "text": _text(part)}
            )
        elif ct == "text/plain" and out["text"] is None:
            out["text"] = _text(part)
        elif ct == "text/html" and out["html"] is None:
            out["html"] = _text(part)
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("path", type=Path, help=".eml file or a directory of them")
    ap.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    a = ap.parse_args(argv)
    if a.path.is_dir():
        data = {p.stem: eml_to_json(p) for p in sorted(a.path.glob("*.eml"))}
    else:
        data = eml_to_json(a.path)
    s = json.dumps(data, indent=2, ensure_ascii=False)
    if a.out:
        a.out.write_text(s + "\n", encoding="utf-8")
    else:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
