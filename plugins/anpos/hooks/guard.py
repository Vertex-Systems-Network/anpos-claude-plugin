#!/usr/bin/env python3
"""PreToolUse hook: keep approval gates in the human's hands.

Blocks the model from:
  * writing captured approval tokens (.anpos/approvals/*),
  * hand-editing consent records (config/consent/consent-requests.json),
  * flipping execution-plan.json to "approved" by editing the file.
Those changes must go through `anpos.py consent ...`, which checks the token
the UserPromptSubmit hook captured from the user's own message.
Exit code 2 = block (stderr is shown to Claude). Exit 0 = no opinion.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path

APPROVED = re.compile(r'"plan_(status|approval_ref)"\s*:\s*"(approved|CONSENT-\d{6})"')


def block(reason: str) -> int:
    print(f"[anpos guard] {reason}", file=sys.stderr)
    return 2


def norm(path: str) -> str:
    return path.replace("\\", "/")


PROTECTED = (".anpos/approvals", "consent-requests.json")
EXEMPT = re.compile(r"anpos\.(py|sh)\b|consent_guard\.py\b")
HARMLESS_REDIRECT = re.compile(r"\d?>&\d|\d?>\s*/dev/null")
READ_ONLY_TOOLS = {"cat", "less", "more", "head", "tail", "ls", "wc", "grep", "rg", "diff", "stat", "file",
                   "sha256sum", "shasum", "md5sum", "find"}
SAFE_GIT = {"add", "commit", "diff", "log", "show", "status", "blame", "ls-files"}
HEREDOC = re.compile(r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?\n\s*\2[ \t]*(?=\n|$)", re.DOTALL)


def split_segments(cmd: str) -> list[tuple[str, str]]:
    """Split on unquoted ; && || | and newlines.

    Returns (raw segment, segment with quoted text blanked) pairs, so a path mentioned inside
    a quoted commit message is not mistaken for a command target.
    """
    out: list[tuple[str, str]] = []
    raw: list[str] = []
    bare: list[str] = []
    quote = ""
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            raw.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(cmd):
                raw.append(cmd[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
                bare.append("Q")
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            raw.append(ch)
            i += 1
            continue
        two = cmd[i:i + 2]
        if two in ("&&", "||") or ch in ";|\n":
            out.append(("".join(raw), "".join(bare)))
            raw, bare = [], []
            i += 2 if two in ("&&", "||") else 1
            continue
        raw.append(ch)
        bare.append(ch)
        i += 1
    out.append(("".join(raw), "".join(bare)))
    return out


def risky_bash(cmd: str) -> str | None:
    """Return a reason when a shell segment could write a protected approval record.

    Reading and committing the files (cat, git add/commit/diff…) stays allowed. This guard
    stops shortcuts and accidents; the real protection is the token + hash check in
    `anpos.py consent confirm`.
    """
    # Heredoc bodies are data, not commands: drop them (the line with the redirect target stays).
    cmd = HEREDOC.sub(lambda m: m.group(1), cmd)
    for segment, bare in split_segments(cmd):
        if not any(p in segment for p in PROTECTED) or EXEMPT.search(segment):
            continue
        words = bare.strip().split()
        first = words[0] if words else ""
        if ">" in HARMLESS_REDIRECT.sub("", bare):
            pass  # redirection into a protected path
        elif first in READ_ONLY_TOOLS and not (first == "find" and ("-delete" in words or "-exec" in words)):
            continue
        elif first == "git" and len(words) > 1 and words[1] in SAFE_GIT:
            continue
        if ".anpos/approvals" in segment:
            return ("Approval tokens are written only by the user's own message (UserPromptSubmit hook). "
                    "Ask the user to type the APPROVE phrase.")
        return "Consent records change only through the anpos CLI (`consent propose|confirm`). Use Read to view them."
    return None


WORKER_AGENT_TYPES = ("anpos:worker", "worker")


def worktree_violation(path: str, agent_type: str) -> str | None:
    """Keep ANPOS workers inside their claimed worktree and the slot's allowed paths."""
    marker = "/.anpos/worktrees/"
    if marker in path:
        root, rest = path.split(marker, 1)
        slot_id, _, rel = rest.partition("/")
        claim_path = Path(root) / ".anpos" / "runtime" / "claims" / f"{slot_id}.json"
        try:
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"No active ANPOS claim for worktree {slot_id}; workers write only in a claimed worktree."
        if not any(fnmatch.fnmatch(rel, p.lstrip("/")) for p in claim.get("allowed_paths") or []):
            return f"{rel} is outside slot {slot_id}'s allowed paths ({', '.join(claim.get('allowed_paths') or [])})."
        if any(fnmatch.fnmatch(rel, p.lstrip("/")) for p in claim.get("denied_paths") or []):
            return f"{rel} is a denied path for slot {slot_id}."
        if not claim.get("control_plane_write"):
            try:
                policy = json.loads((Path(root) / "config/security/control-plane-policy.json").read_text(encoding="utf-8"))
                protected = [p.lstrip("/") for p in policy.get("protected_paths") or []]
            except (OSError, json.JSONDecodeError):
                protected = []
            if any(fnmatch.fnmatch(rel, p) or rel == p for p in protected):
                return f"{rel} is protected ANPOS control plane; workers can't change it."
        return None
    if agent_type in WORKER_AGENT_TYPES or agent_type.endswith(":worker"):
        return "ANPOS workers write only inside their claimed worktree (.anpos/worktrees/<slot>/)."
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool = data.get("tool_name") or ""
    inp = data.get("tool_input") or {}

    if tool == "Bash":
        reason = risky_bash(norm(str(inp.get("command") or "")))
        return block(reason) if reason else 0

    if tool not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return 0
    path = norm(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    worktree_reason = worktree_violation(path, str(data.get("agent_type") or ""))
    if worktree_reason:
        return block(worktree_reason)
    if "/.anpos/approvals/" in path or path.startswith(".anpos/approvals/"):
        return block("Approval tokens can't be written by Claude. The user must type the APPROVE phrase themselves.")
    if path.endswith("config/consent/consent-requests.json"):
        return block("Consent records change only through `anpos.py consent propose|confirm`.")
    if path.endswith("config/ai/execution-plan.json"):
        if tool == "Write":
            new = str(inp.get("content") or "")
            current = ""
            try:
                current = Path(path).read_text(encoding="utf-8")
            except OSError:
                pass
            try:
                was_approved = json.loads(current).get("plan_status") == "approved" if current else False
            except json.JSONDecodeError:
                was_approved = False
            if APPROVED.search(new) and not was_approved:
                return block("Plan approval is recorded only by `anpos.py consent confirm` after the user types "
                             "the APPROVE PLAN phrase. Keep plan_status as draft/proposed in your edit.")
        else:
            edits = inp.get("edits") if tool == "MultiEdit" else [inp]
            for e in edits or []:
                if APPROVED.search(str(e.get("new_string") or "")) and not APPROVED.search(str(e.get("old_string") or "")):
                    return block("Plan approval is recorded only by `anpos.py consent confirm` after the user types "
                                 "the APPROVE PLAN phrase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
