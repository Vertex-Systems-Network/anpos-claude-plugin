#!/usr/bin/env python3
"""Circuit breaker for repeated identical failing shell commands (ANPOS runtime budgets).

Events (one script, routed by hook_event_name):
  PostToolUseFailure (Bash) → count a failure of that exact command
  PostToolUse (Bash)        → the command succeeded: clear its count
  PreToolUse (Bash)         → block the command once it failed max_same_error_retries times

Active only in ANPOS projects (.anpos/ or config/runtime/budgets.json present). The limit comes from
config/runtime/budgets.json limits.max_same_error_retries (default 3). Reset: `anpos breaker reset`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def project_root(data: dict) -> Path | None:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    if (root / ".anpos").is_dir() or (root / "config/runtime/budgets.json").exists():
        return root
    return None


def limit(root: Path) -> int:
    try:
        value = json.loads((root / "config/runtime/budgets.json").read_text())["limits"]["max_same_error_retries"]
        return max(1, int(value))
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return 3


def failed(response) -> bool:
    """Some clients report a non-zero exit as a normal PostToolUse; detect that too."""
    if not isinstance(response, dict):
        return False
    if response.get("is_error") is True or response.get("isError") is True:
        return True
    for key in ("exit_code", "exitCode", "returncode", "return_code", "code"):
        value = response.get(key)
        if isinstance(value, int) and value != 0:
            return True
    return False


def signature(data: dict) -> str:
    command = " ".join(str((data.get("tool_input") or {}).get("command") or "").split())
    scope = data.get("agent_id") or "main"
    return hashlib.sha256(f"{scope}\0{command}".encode()).hexdigest()[:16]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    root = project_root(data)
    if root is None:
        return 0
    path = root / ".anpos" / "runtime" / "breaker.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    sig = signature(data)
    event = data.get("hook_event_name")
    command = str((data.get("tool_input") or {}).get("command") or "")
    if event == "PreToolUse":
        entry = state.get(sig)
        if entry and entry.get("count", 0) >= limit(root):
            print(f"[anpos circuit breaker] This exact command already failed {entry['count']} times "
                  f"(limit {limit(root)} from config/runtime/budgets.json). Stop retrying: fix the cause, change approach, "
                  "or record a blocker (`anpos wu <id> blocked --blocker \"…\"`) and tell the user. "
                  "The user can reset with `anpos breaker reset`.", file=sys.stderr)
            return 2
        return 0
    if event == "PostToolUseFailure":
        entry = state.setdefault(sig, {"count": 0, "command": command[:300]})
        entry["count"] += 1
        entry["last_failure_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    elif event == "PostToolUse" and failed(data.get("tool_response")):
        entry = state.setdefault(sig, {"count": 0, "command": command[:300]})
        entry["count"] += 1
        entry["last_failure_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    elif event == "PostToolUse":
        if sig not in state:
            return 0
        state.pop(sig, None)
    else:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
