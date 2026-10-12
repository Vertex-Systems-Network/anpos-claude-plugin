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


def is_project(path: Path) -> bool:
    return (path / ".anpos").is_dir() or (path / "config/ai/execution-plan.json").exists()


def has_pending_code(root: Path, code: str) -> bool:
    try:
        doc = json.loads((root / "config/consent/consent-requests.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(r.get("status") == "pending" and str(r.get("nonce", "")).replace("-", "")[:8].upper() == code
               for r in doc.get("requests", []))


def find_project(data: dict, code: str) -> Path | None:
    """Find the ANPOS project the typed code belongs to.

    1. the session folder itself (CLAUDE_PROJECT_DIR, then the hook's cwd) — the classic case;
    2. parents of those folders and the pointer `consent propose` left for this code — accepted only when that project
       really has a pending request with this code (a session opened above or inside the project).
    If no project has the code pending, fall back to the session folder when it is a project (consent confirm then
    reports the mismatch to the user)."""
    direct: list[Path] = []
    extra: list[Path] = []
    for raw in (os.environ.get("CLAUDE_PROJECT_DIR"), data.get("cwd")):
        if raw:
            start = Path(raw).resolve()
            direct.append(start)
            extra += list(start.parents)
    pointer_home = Path(os.environ.get("ANPOS_POINTER_HOME") or (Path.home() / ".claude" / "anpos" / "pending"))
    try:
        pointed = json.loads((pointer_home / f"{code}.json").read_text(encoding="utf-8")).get("root")
        if pointed:
            extra.append(Path(pointed).resolve())
    except (OSError, ValueError):
        pass
    for project in dict.fromkeys(direct + extra):
        if is_project(project) and has_pending_code(project, code):
            return project
    return next((d for d in direct if is_project(d)), None)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = str(data.get("prompt") or data.get("user_prompt") or "")
    match = PHRASE.search(prompt)
    if not match:
        return 0
    decision, kind, code = match.group(1).lower(), match.group(2).upper(), match.group(3).upper()
    root = find_project(data, code)
    if root is None:
        return 0  # not an ANPOS session and no project waits for this code: stay silent
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
