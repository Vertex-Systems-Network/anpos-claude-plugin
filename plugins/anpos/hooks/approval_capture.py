#!/usr/bin/env python3
"""UserPromptSubmit hook: capture a human approval/rejection phrase.

Only text the user actually submits reaches this hook, so the token it writes
is evidence that the human (not the model) typed the decision. The model is
blocked from writing these tokens itself by hooks/guard.py.

Phrases (case-insensitive, may appear anywhere in the message):
    APPROVE PLAN 1A2B3C4D   REJECT PLAN 1A2B3C4D
    APPROVE STACK ...       APPROVE CHANGE ...       APPROVE RELEASE ...
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import re

PHRASE = re.compile(r"\b(APPROVE|REJECT)\s+(PLAN|STACK|CHANGE|RELEASE)\s+([0-9A-F]{8})\b", re.IGNORECASE)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = str(data.get("prompt") or data.get("user_prompt") or "")
    match = PHRASE.search(prompt)
    if not match:
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    if not ((root / ".anpos").is_dir() or (root / "config/ai/execution-plan.json").exists()):
        return 0
    decision, kind, code = match.group(1).lower(), match.group(2).upper(), match.group(3).upper()
    folder = root / ".anpos" / "approvals"
    folder.mkdir(parents=True, exist_ok=True)
    token_path = folder / f"{code}.json"
    if token_path.exists():
        print(f"[anpos] A decision for code {code} was already captured earlier; the first one stands.")
        return 0
    token = {
        "code": code,
        "kind": kind,
        "decision": decision,
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "session_id": data.get("session_id"),
        "prompt_id": data.get("prompt_id"),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "hook": "UserPromptSubmit",
        "plugin": "anpos",
        "consumed_at": None,
    }
    token_path.write_text(json.dumps(token, indent=2) + "\n", encoding="utf-8")
    print(f"[anpos] The user typed `{decision.upper()} {kind} {code}`. Decision captured. "
          f"Now run the anpos `consent confirm` command to record it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
