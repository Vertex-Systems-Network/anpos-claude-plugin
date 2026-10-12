#!/usr/bin/env python3
"""ANPOS Planner CLI for the `anpos` Claude Code plugin.

Free, local, subscription-free implementation of the ANPOS intake -> research ->
plan -> approval flow (START-HERE Stages 1-7) plus the post-plan stage gates.

The script only reads and writes planner files inside the target repository.
It never contacts a network service and never touches billing/entitlements.

Usage: python3 anpos.py <command> [options]   (run `python3 anpos.py -h`)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.modules.setdefault("anpos", sys.modules[__name__])
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = PLUGIN_ROOT / "templates"
SCHEMAS = PLUGIN_ROOT / "schemas"
PLUGIN_VERSION = "1.0.0"
PROTOCOL_VERSION = "1.4.0"
CANONICAL_SOURCE = "vertex-systems-network/ai-native-project-operating-system"

F_IDEA = "PROJECT-IDEA.md"
F_PREPLAN = "docs/ai/PRE-PLAN.md"
F_STATE = "config/ai/project-state.json"
F_EVID = "config/research/evidence-registry.json"
F_OPT = "config/ai/options-bank.json"
F_MOD = "config/ai/modules-bank.json"
F_PLAN = "config/ai/execution-plan.json"
F_TRACE = "config/traceability/requirements-traceability.json"
F_RISK = "config/risk/risk-register.json"
F_CONSENT = "config/consent/consent-requests.json"
F_MARKER = ".anpos/planner.json"
APPROVALS_DIR = ".anpos/approvals"

PLANNER_FILES = [F_IDEA, F_PREPLAN, F_STATE, F_EVID, F_OPT, F_MOD, F_PLAN, F_TRACE, F_RISK, F_CONSENT]
SCHEMA_FOR = {
    F_STATE: "project-state.schema.json",
    F_EVID: "research-evidence.schema.json",
    F_OPT: "options-bank.schema.json",
    F_MOD: "modules-bank.schema.json",
    F_PLAN: "execution-plan.schema.json",
    F_TRACE: "requirements-traceability.schema.json",
    F_RISK: "risk-register.schema.json",
    F_CONSENT: "consent-requests.schema.json",
}

STAGES = [
    "not_started", "initialization", "intake", "research", "planning", "plan_proposed", "plan_approved",
    "system_design", "technology_consent", "architecture", "data_flow", "ui_ux", "development", "sqa",
    "security", "release", "operations", "maintenance",
]
STAGE_INDEX = {s: i for i, s in enumerate(STAGES)}
SCRIPT_ONLY_STAGES = {"plan_proposed", "plan_approved"}
APPROVED_STATES = {"approved", "implemented", "verified"}
DONE_WU = {"complete", "deprecated"}
CONSENT_KIND = {"plan_approval": "PLAN", "technology_stack": "STACK", "scope_change": "CHANGE",
                "release_candidate": "RELEASE"}
KIND_TO_TYPE = {v: k for k, v in CONSENT_KIND.items()}


class AnposError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers

def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd().resolve()


def read_json(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AnposError(f"missing file: {rel} (run `init` first)")
    except json.JSONDecodeError as exc:
        raise AnposError(f"{rel} is not valid JSON: {exc}")


def write_json(root: Path, rel: str, doc: Any) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".anpos-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def git_remote_slug(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    if not out:
        return None
    out = out.removesuffix(".git").rstrip("/")
    match = re.search(r"[:/]([^/:]+/[^/:]+)$", out)
    return match.group(1) if match else out


def emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- detect / init

def detect(root: Path) -> dict[str, Any]:
    slug = git_remote_slug(root)
    instance_path = root / "config/protocol/instance.json"
    instance: dict[str, Any] = {}
    if instance_path.exists():
        try:
            instance = json.loads(instance_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            instance = {"instance_status": "malformed"}
    present = [f for f in PLANNER_FILES if (root / f).exists()]
    missing = [f for f in PLANNER_FILES if f not in present]
    status = instance.get("instance_status")
    notes: list[str] = []

    if status == "template_source":
        if slug and slug.lower() == CANONICAL_SOURCE:
            state = "canonical_source"
            action = "Refuse. The canonical ANPOS source is an inert template. Create a new repository from it and run the plugin there."
        else:
            state = "uninitialized_child"
            action = "Run the repository's own child bootstrap (scripts/bootstrap_child.py, dry run first, then --apply) before planning."
            if not slug:
                notes.append("No git remote found; could not prove this is not the canonical source. Confirm with the user.")
    elif status == "active_project":
        state = "active_project"
        action = "Full ANPOS child. Use its existing planner files; reconcile with repository reality and resume."
    elif status == "malformed":
        state = "partial_or_malformed"
        action = "config/protocol/instance.json is not valid JSON. Stop and repair it before planning."
    elif (root / F_MARKER).exists():
        state = "planner_lite"
        action = "ANPOS planner already installed by this plugin. Resume from the current lifecycle stage."
    else:
        meaningful = [p for p in root.iterdir() if p.name not in {".git", ".anpos"}] if root.exists() else []
        state = "empty_repository" if not meaningful else "not_anpos"
        action = "Install the ANPOS planner files with `init` (never overwrites existing files)."

    if state in {"active_project", "planner_lite"} and missing:
        notes.append("Some planner files are missing: " + ", ".join(missing))
    return {
        "state": state,
        "root": str(root),
        "git_remote": slug,
        "instance_status": status,
        "planner_files_present": present,
        "planner_files_missing": missing,
        "recommended_action": action,
        "notes": notes,
    }


def cmd_detect(args: argparse.Namespace) -> int:
    emit(detect(find_root(args.root)), True)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    if getattr(args, "full", False):
        return cmd_init_full(args)
    root = find_root(args.root)
    info = detect(root)
    state = info["state"]
    if state == "canonical_source":
        print("REFUSED: this is the canonical ANPOS template source. Create a new repository from it and run /anpos:start there.")
        return 2
    if state == "uninitialized_child":
        print("This repository was copied from the ANPOS template and is not bootstrapped yet.")
        print("Run its own bootstrap first (show the user the dry run before --apply):")
        print("  python3 scripts/bootstrap_child.py --help")
        return 3
    if state == "partial_or_malformed":
        print(info["recommended_action"])
        return 4
    if state == "active_project":
        print("Full ANPOS child project detected. Nothing installed; the plugin will use the existing files.")
        if info["planner_files_missing"]:
            print("Missing planner files (repair through the project's own ANPOS flow): " + ", ".join(info["planner_files_missing"]))
        return 0

    created, kept = [], []
    for rel in PLANNER_FILES:
        target = root / rel
        if target.exists():
            kept.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATES / rel, target)
        created.append(rel)
    marker = root / F_MARKER
    if not marker.exists():
        write_json(root, F_MARKER, {
            "schema_version": 1,
            "mode": "planner_lite",
            "plugin": "anpos",
            "plugin_version": PLUGIN_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "project_name": args.project_name,
            "installed_at": iso(now()),
            "note": "Installed by the ANPOS Claude Code plugin. Free mode: no commercial licensing or entitlement checks.",
        })
        created.append(F_MARKER)
    (root / APPROVALS_DIR).mkdir(parents=True, exist_ok=True)
    ensure_anpos_gitignore(root)
    keep = root / APPROVALS_DIR / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")
    state_doc = read_json(root, F_STATE)
    if state_doc.get("lifecycle_stage") == "not_started":
        state_doc["lifecycle_stage"] = "initialization"
        write_json(root, F_STATE, state_doc)
    print(f"ANPOS planner installed in {root}")
    for rel in created:
        print(f"  created  {rel}")
    for rel in kept:
        print(f"  kept     {rel} (already existed, not touched)")
    return 0


# ---------------------------------------------------------------- full ANPOS mode (bootstrap / adoption)

TEMPLATE_CHILD = PLUGIN_ROOT / "template" / "anpos-child"
TEMPLATE_SOURCE = PLUGIN_ROOT / "template" / "SOURCE.json"
APPEND_LINES = {".gitignore"}
ADAPTER_SECTIONS = {"CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md"}
SECTION_BEGIN = "<!-- ANPOS:BEGIN (added by the anpos plugin; keep this block) -->"
SECTION_END = "<!-- ANPOS:END -->"
ADOPTION_REVIEW_DIR = "docs/anpos/adoption"


def ensure_anpos_gitignore(root: Path) -> None:
    gi = root / ".anpos" / ".gitignore"
    gi.parent.mkdir(parents=True, exist_ok=True)
    if not gi.exists():
        gi.write_text("runtime/\nworktrees/\n", encoding="utf-8")


def template_files() -> list[str]:
    if not TEMPLATE_CHILD.is_dir():
        raise AnposError("the bundled ANPOS template is missing from the plugin (template/anpos-child)")
    return sorted(p.relative_to(TEMPLATE_CHILD).as_posix() for p in TEMPLATE_CHILD.rglob("*") if p.is_file())


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AnposError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def classify_adoption(root: Path) -> dict[str, list[str]]:
    """Compare the bundled template with the target tree.

    add            → file absent in the project, copied
    same           → identical already
    merge_lines    → .gitignore: missing lines appended
    merge_section  → agent adapter files: ANPOS block appended
    keep_state     → planner files the project already filled (kept as-is)
    keep_project   → project-owned file that differs: never overwritten; ANPOS copy saved for review
    """
    plan: dict[str, list[str]] = {k: [] for k in ("add", "same", "merge_lines", "merge_section", "keep_state", "keep_project")}
    for rel in template_files():
        target = root / rel
        if not target.exists():
            plan["add"].append(rel)
        elif target.read_bytes() == (TEMPLATE_CHILD / rel).read_bytes():
            plan["same"].append(rel)
        elif rel in APPEND_LINES:
            plan["merge_lines"].append(rel)
        elif rel in ADAPTER_SECTIONS:
            plan["merge_section"].append(rel)
        elif rel in PLANNER_FILES:
            plan["keep_state"].append(rel)
        else:
            plan["keep_project"].append(rel)
    return plan


def cmd_init_full(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    info = detect(root)
    state = info["state"]
    if state == "canonical_source":
        print("REFUSED: this is the canonical ANPOS template source.")
        return 2
    if state == "active_project":
        print("Already a full ANPOS child project. Nothing to do.")
        return 0
    if state in {"uninitialized_child", "partial_or_malformed"}:
        print(f"State {state}: {info['recommended_action']}")
        return 3
    if not (root / ".git").exists():
        raise AnposError("full mode needs a git repository: run `git init` (and add the remote if you have one) first")
    slug = args.repository or info["git_remote"]
    if not slug or "/" not in slug:
        raise AnposError("could not determine owner/repository from the git remote. Pass --repository owner/name")
    if slug.lower() == CANONICAL_SOURCE:
        raise AnposError("refusing to bootstrap the canonical ANPOS source")
    owner = (args.github_owner or slug.split("/", 1)[0]).lstrip("@")
    project_name = args.project_name or slug.split("/", 1)[1].replace("-", " ").title()

    plan = classify_adoption(root)
    has_commits = bool(git(root, "rev-parse", "--verify", "HEAD", check=False))
    adopting = state == "not_anpos" or state == "planner_lite"
    summary = {k: len(v) for k, v in plan.items()}

    if args.dry_run:
        emit({"state": state, "repository": slug, "github_owner": owner, "project_name": project_name,
              "summary": summary,
              "merge_lines": plan["merge_lines"], "merge_section": plan["merge_section"],
              "keep_state": plan["keep_state"], "keep_project": plan["keep_project"],
              "branch": ("anpos/adopt" if adopting and has_commits and not args.no_branch else None)}, True)
        return 0

    if has_commits and git(root, "status", "--porcelain"):
        raise AnposError("the working tree has uncommitted changes. Commit or stash them first so the adoption is reviewable")
    branch = None
    if adopting and has_commits and not args.no_branch:
        branch = args.branch or "anpos/adopt"
        if git(root, "rev-parse", "--verify", branch, check=False):
            raise AnposError(f"branch {branch} already exists; pass --branch <name>")
        git(root, "checkout", "-q", "-b", branch)

    # Snapshot everything the bootstrap or the copy could touch that the project already owns.
    protected = plan["keep_project"] + plan["keep_state"] + plan["merge_lines"] + plan["merge_section"]
    protected += [f for f in PLANNER_FILES if (root / f).exists() and f not in protected]
    for folder in (".github", ".ai"):  # bootstrap generates CODEOWNERS, dependabot.yml and workflows here
        if (root / folder).is_dir():
            protected += [p.relative_to(root).as_posix() for p in (root / folder).rglob("*")
                          if p.is_file() and p.relative_to(root).as_posix() not in protected]
    untouched = set(plan["same"]) | set(plan["add"])
    snapshot = {rel: (root / rel).read_bytes() for rel in protected if rel not in untouched}

    for rel in plan["add"] + plan["merge_lines"] + plan["merge_section"] + plan["keep_project"] + plan["keep_state"]:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEMPLATE_CHILD / rel, target)

    cmd = [sys.executable, "scripts/bootstrap_child.py", "--repository", slug, "--project-name", project_name,
           "--github-owner", owner, "--apply"]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        for rel, data in snapshot.items():
            (root / rel).write_bytes(data)
        raise AnposError("ANPOS child bootstrap failed (project files restored):\n" + (proc.stderr or proc.stdout))

    # Files bootstrap regenerated that the project already had (e.g. CODEOWNERS, dependabot.yml).
    regenerated = [rel for rel in snapshot if rel not in plan["keep_project"] and rel not in plan["keep_state"]
                   and rel not in plan["merge_lines"] and rel not in plan["merge_section"]]
    review: list[str] = []
    for rel in plan["keep_project"] + regenerated:
        anpos_version = (root / rel).read_bytes()
        if anpos_version != snapshot[rel]:
            copy = root / ADOPTION_REVIEW_DIR / (rel + ".anpos")
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(anpos_version)
            review.append(rel)
        (root / rel).write_bytes(snapshot[rel])
    for rel in plan["keep_state"]:
        (root / rel).write_bytes(snapshot[rel])
    for rel in plan["merge_lines"]:
        original = snapshot[rel].decode("utf-8", errors="replace")
        have = {line.strip() for line in original.splitlines()}
        extra = [line for line in (TEMPLATE_CHILD / rel).read_text(encoding="utf-8").splitlines()
                 if line.strip() and line.strip() not in have]
        text = original.rstrip("\n") + "\n"
        if extra:
            text += "\n# --- added by ANPOS adoption ---\n" + "\n".join(extra) + "\n"
        (root / rel).write_text(text, encoding="utf-8")
    for rel in plan["merge_section"]:
        original = snapshot[rel].decode("utf-8", errors="replace")
        if SECTION_BEGIN not in original:
            block = (TEMPLATE_CHILD / rel).read_text(encoding="utf-8").strip()
            original = original.rstrip("\n") + f"\n\n{SECTION_BEGIN}\n{block}\n{SECTION_END}\n"
        (root / rel).write_text(original, encoding="utf-8")

    source = json.loads(TEMPLATE_SOURCE.read_text()) if TEMPLATE_SOURCE.exists() else {}
    marker = {}
    if (root / F_MARKER).exists():
        marker = json.loads((root / F_MARKER).read_text(encoding="utf-8"))
    marker.update({
        "schema_version": 1, "mode": "full", "plugin": "anpos", "plugin_version": PLUGIN_VERSION,
        "protocol_version": source.get("protocol_version", PROTOCOL_VERSION),
        "template_commit": source.get("upstream_commit"), "project_name": project_name,
        "full_installed_at": iso(now()), "adoption_branch": branch, "kept_for_review": review,
        "project_workflows": sorted(p.name for p in (root / ".github/workflows").glob("*.y*ml")
                                    if p.name not in {Path(t).name for t in template_files() if t.startswith(".github/workflows/")}
                                    and (".github/workflows/" + p.name) in snapshot),
        "note": "Full ANPOS child installed by the anpos plugin. Commercial licensing files from upstream stay inert.",
    })
    marker.setdefault("installed_at", marker["full_installed_at"])
    write_json(root, F_MARKER, marker)
    (root / APPROVALS_DIR).mkdir(parents=True, exist_ok=True)
    (root / APPROVALS_DIR / ".gitkeep").touch()
    ensure_anpos_gitignore(root)

    state_doc = read_json(root, F_STATE)
    if state_doc.get("lifecycle_stage") in {None, "not_started"}:
        state_doc["lifecycle_stage"] = "initialization"
        write_json(root, F_STATE, state_doc)

    report = [
        "# ANPOS adoption report", "",
        f"- Repository: `{slug}` · GitHub owner for CODEOWNERS: `@{owner}`",
        f"- ANPOS template: protocol {marker['protocol_version']}, upstream commit `{marker.get('template_commit')}`",
        f"- Branch: `{branch}`" if branch else "- Branch: current branch (repository had no commits or --no-branch)",
        "", "## Summary", "",
        f"- Added: {len(plan['add'])} files",
        f"- Already identical: {len(plan['same'])}",
        f"- `.gitignore` lines merged: {', '.join(plan['merge_lines']) or 'none'}",
        f"- ANPOS block appended to: {', '.join(plan['merge_section']) or 'none'}",
        f"- Existing planner files kept: {', '.join(plan['keep_state']) or 'none'}",
        "", "## Project files kept unchanged (review needed)", "",
    ]
    report += [f"- `{rel}` — ANPOS version saved as `{ADOPTION_REVIEW_DIR}/{rel}.anpos`" for rel in review] or ["- none"]
    report += ["", "Nothing in the project was overwritten. Merge the `.anpos` copies by hand only where you want ANPOS behaviour "
               "(for example ANPOS protected-path rules in CODEOWNERS)."]
    if adopting or review:
        (root / "docs/anpos").mkdir(parents=True, exist_ok=True)
        (root / "docs/anpos/ADOPTION-REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    emit({"result": "full_anpos_installed", "repository": slug, "branch": branch, "summary": summary,
          "kept_for_review": review, "report": "docs/anpos/ADOPTION-REPORT.md" if (adopting or review) else None,
          "next": "Review `git status`, run `validate`, commit, and open a PR if this was an adoption branch."}, True)
    return 0


# ---------------------------------------------------------------- stages

def plan_is_approved(root: Path) -> tuple[bool, str]:
    plan = read_json(root, F_PLAN)
    consents = read_json(root, F_CONSENT)
    ref = plan.get("plan_approval_ref")
    if plan.get("plan_status") != "approved" or not ref:
        return False, "the project plan is not approved"
    for req in consents.get("requests", []):
        if req.get("id") == ref and req.get("type") == "plan_approval" and req.get("status") in APPROVED_STATES:
            return True, ""
    return False, f"plan_approval_ref {ref} does not name an approved plan_approval consent"


def stack_is_approved(root: Path) -> bool:
    consents = read_json(root, F_CONSENT)
    return any(r.get("type") == "technology_stack" and r.get("status") in APPROVED_STATES
               for r in consents.get("requests", []))


def cmd_stage(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    target = args.stage
    if target not in STAGE_INDEX:
        raise AnposError(f"unknown stage {target}. Valid: {', '.join(STAGES)}")
    if target in SCRIPT_ONLY_STAGES:
        raise AnposError(f"{target} can only be reached through `consent propose` / `consent confirm`.")
    state = read_json(root, F_STATE)
    plan = read_json(root, F_PLAN)
    current = state.get("lifecycle_stage", "not_started")

    if STAGE_INDEX[target] > STAGE_INDEX["plan_approved"]:
        ok, why = plan_is_approved(root)
        if not ok:
            raise AnposError(f"cannot enter {target}: {why}. Finish Stage 7 and get plan approval first.")
        if STAGE_INDEX[target] > STAGE_INDEX["technology_consent"] and not stack_is_approved(root):
            raise AnposError(f"cannot enter {target}: no approved technology_stack consent (Stage 9 gate). "
                             "Propose it with `consent propose --type technology_stack`.")
        if STAGE_INDEX[target] > STAGE_INDEX["release"] and not consent_approved(root, "release_candidate"):
            raise AnposError(f"cannot enter {target}: no approved release_candidate consent (release gate). "
                             "Propose it with `consent propose --type release_candidate`.")

    lifecycle_forward = (current in STAGE_INDEX and STAGE_INDEX[target] > STAGE_INDEX[current]
                         and STAGE_INDEX[current] >= STAGE_INDEX["plan_approved"])
    check = None
    if lifecycle_forward:
        expected = STAGES[STAGE_INDEX[current] + 1]
        if target != expected:
            raise AnposError(f"lifecycle stages run in order: after {current} comes {expected}")
        if args.not_applicable:
            if current not in NA_STAGES:
                raise AnposError(f"{current} cannot be marked not applicable (only {', '.join(sorted(NA_STAGES))})")
            check = {"result": "not_applicable", "reason": args.not_applicable}
        else:
            problems = exit_problems(root, current)
            if problems:
                raise AnposError(f"exit check for {current} failed:\n  - " + "\n  - ".join(problems) +
                                 (f"\n(If {current} truly does not apply, use --not-applicable \"reason\".)"
                                  if current in NA_STAGES else ""))
            check = {"result": "passed"}

    if target == "planning" and plan.get("plan_status") in {"proposed", "approved"}:
        if not args.revise:
            raise AnposError("the plan is already proposed/approved. Returning to planning is a material revision: "
                             "re-run with --revise (the plan will need a new approval).")
        plan["plan_status"] = "draft"
        plan["plan_approval_ref"] = None
        close_pending(root, "plan_approval", "plan returned to planning for revision")
        write_json(root, F_PLAN, plan)

    state["lifecycle_stage"] = target
    write_json(root, F_STATE, state)
    log_stage(root, current, target, check)
    print(f"lifecycle_stage: {current} -> {target}" + (f" ({check['result']})" if check else ""))
    return 0


# ---------------------------------------------------------------- stage exit checks (Stages 8-17)

F_STAGE_LOG = ".anpos/stage-log.json"
NA_STAGES = {"data_flow", "ui_ux", "operations"}
LIFECYCLE_DOCS = {
    "system_design": "docs/ai/SYSTEM-DESIGN.md", "architecture": "docs/ai/ARCHITECTURE.md",
    "data_flow": "docs/ai/DATA-FLOW.md", "ui_ux": "docs/ai/UI-UX.md", "sqa": "docs/ai/QA-REPORT.md",
    "security": "docs/ai/SECURITY-REVIEW.md", "release": "docs/ai/RELEASE.md", "operations": "docs/ai/OPERATIONS.md",
}
SENSITIVE_CLASSES = {"confidential", "restricted", "secret"}
THREAT_DONE = {"mitigated", "resolved", "closed", "accepted", "not_applicable"}


def consent_approved(root: Path, ctype: str) -> bool:
    consents = read_json(root, F_CONSENT)
    return any(r.get("type") == ctype and r.get("status") in APPROVED_STATES for r in consents.get("requests", []))


def log_stage(root: Path, current: str, target: str, check: dict[str, Any] | None) -> None:
    path = root / F_STAGE_LOG
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log = {"schema_version": 1, "entries": []}
    log["entries"].append({"at": iso(now()), "from": current, "to": target, "exit_check": check,
                           "repository_ref": git(root, "rev-parse", "HEAD", check=False) or None})
    write_json(root, F_STAGE_LOG, log)


def _optional_json(root: Path, rel: str) -> dict[str, Any] | None:
    try:
        return json.loads((root / rel).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def exit_problems(root: Path, stage: str) -> list[str]:
    """What still blocks leaving a lifecycle stage. Full ANPOS children are checked against their structured files."""
    problems: list[str] = []
    doc = LIFECYCLE_DOCS.get(stage)
    if doc:
        path = root / doc
        if not path.exists():
            problems.append(f"write {doc}")
        elif len(path.read_text(encoding="utf-8").strip()) < 300:
            problems.append(f"{doc} is too thin to count as the stage output")
    state = read_json(root, F_STATE)
    full = is_full_child(root)

    if stage == "system_design" and full:
        tm = _optional_json(root, "config/security/threat-model.json") or {}
        for key in ("assets", "actors", "entry_points", "threats"):
            if not tm.get(key):
                problems.append(f"config/security/threat-model.json: add {key}")
        if str(tm.get("status", "")).startswith("pending"):
            problems.append("config/security/threat-model.json: set status to active once the model is project-specific")
        dg = _optional_json(root, "config/data/data-governance.json") or {}
        if "data_records" not in dg:
            problems.append("config/data/data-governance.json: add a data_records list (copy data_record_template per data "
                            "set; [] only with a data_scope_note when nothing is stored)")
        elif not dg["data_records"] and not dg.get("data_scope_note"):
            problems.append("config/data/data-governance.json: data_records is empty — add records or a data_scope_note")
        if str(dg.get("status", "")).startswith("pending"):
            problems.append("config/data/data-governance.json: set status to active")
    elif stage == "technology_consent":
        if not stack_is_approved(root):
            problems.append("technology stack not approved (APPROVE STACK)")
    elif stage == "architecture" and full:
        adr = _optional_json(root, "config/architecture/decision-records.json") or {}
        if not any(d.get("status") == "accepted" for d in adr.get("decisions", [])):
            problems.append("config/architecture/decision-records.json: record at least one accepted architecture decision")
    elif stage == "data_flow" and full:
        dg = _optional_json(root, "config/data/data-governance.json") or {}
        for rec in dg.get("data_records", []):
            if rec.get("classification") in SENSITIVE_CLASSES and (not rec.get("retention") or not rec.get("deletion_method")):
                problems.append(f"data record {rec.get('id')}: sensitive data needs retention and deletion_method")
    elif stage == "ui_ux" and full:
        da = _optional_json(root, "config/design/design-assurance.json") or {}
        if not (da.get("verification") or {}).get("responsive_targets"):
            problems.append("config/design/design-assurance.json: set verification.responsive_targets")
    elif stage == "development":
        units = read_json(root, F_PLAN).get("work_units", [])
        open_units = [w["id"] for w in units if w.get("status") not in DONE_WU | {"deferred"}]
        if open_units:
            problems.append("unfinished work units: " + ", ".join(open_units[:10]) + (" …" if len(open_units) > 10 else ""))
        no_evidence = [w["id"] for w in units if w.get("status") == "complete" and not w.get("completion_evidence")]
        if no_evidence:
            problems.append("complete work units without evidence: " + ", ".join(no_evidence))
        if not any(w.get("status") == "complete" for w in units):
            problems.append("no work unit is complete yet")
    elif stage == "sqa":
        if state.get("qa_readiness") != "ready":
            problems.append("config/ai/project-state.json: qa_readiness must be \"ready\" (after checks pass)")
        if state.get("critical_defects"):
            problems.append("config/ai/project-state.json: critical_defects must be fixed and cleared")
    elif stage == "security":
        if state.get("security_readiness") != "ready":
            problems.append("config/ai/project-state.json: security_readiness must be \"ready\"")
        if full:
            tm = _optional_json(root, "config/security/threat-model.json") or {}
            for t in tm.get("threats", []):
                if str(t.get("impact", "")).lower() in {"high", "critical"} and str(t.get("status", "")).lower() not in THREAT_DONE:
                    problems.append(f"threat {t.get('id')} ({t.get('impact')}) is still {t.get('status')}")
    elif stage == "release":
        if not consent_approved(root, "release_candidate"):
            problems.append("release candidate not approved (APPROVE RELEASE)")
        if full:
            import anpos_govern
            problems += anpos_govern.assurance_release_problems(root)
        if full and not (_optional_json(root, "config/release/release-policy.json") or {}).get("environments"):
            problems.append("config/release/release-policy.json: add environments (copy environment_template)")
    elif stage == "operations" and full:
        ops = _optional_json(root, "config/operations/operations-policy.json") or {}
        rb = _optional_json(root, "config/operations/runbooks-and-drills.json") or {}
        recovery = ops.get("recovery") or {}
        if not (recovery.get("rto") and recovery.get("rpo")) and not rb.get("runbooks"):
            problems.append("set operations-policy recovery rto/rpo or add at least one runbook")
    return problems


def cmd_stage_check(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    stage = args.stage or read_json(root, F_STATE).get("lifecycle_stage")
    problems = exit_problems(root, stage)
    if args.json:
        emit({"stage": stage, "ready_to_exit": not problems, "problems": problems}, True)
    else:
        print(f"{stage}: " + ("ready to move on" if not problems else "not ready"))
        for p in problems:
            print(f"  - {p}")
    return 0 if not problems else 1


# ---------------------------------------------------------------- evidence

def cmd_evidence_add(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    doc = read_json(root, F_EVID)
    entries = doc.setdefault("entries", [])
    highest = 0
    for entry in entries:
        match = re.fullmatch(r"EVID-(\d{6})", str(entry.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    entry = {
        "id": f"EVID-{highest + 1:06d}",
        "claim_or_question": args.claim,
        "source_type": args.source_type,
        "source_ref": args.source_ref,
        "source_title": args.title,
        "publisher_or_owner": args.publisher,
        "published_at": args.published_at,
        "accessed_at": args.accessed_at or now().date().isoformat(),
        "source_revision_or_hash": None,
        "evidence_class": args.evidence_class,
        "confidence": args.confidence,
        "summary": args.summary,
        "supports_requirement_ids": sorted(set(args.req or [])),
        "supports_option_ids": sorted(set(args.opt or [])),
        "supports_module_ids": sorted(set(args.mod or [])),
        "validation_status": args.validation_status,
        "supersedes": sorted(set(args.supersedes or [])),
        "notes": args.note or [],
    }
    entries.append(entry)
    write_json(root, F_EVID, doc)
    print(entry["id"])
    return 0


# ---------------------------------------------------------------- plan digest

def _strip(obj: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if k not in keys}


def plan_digest(root: Path) -> str:
    """Hash of the approvable plan content.

    Execution progress fields (status, evidence, blockers) and the approval
    fields themselves are excluded so that doing the work does not invalidate
    the approval, while any change to scope, structure or acceptance criteria does.
    """
    plan = read_json(root, F_PLAN)
    modules = read_json(root, F_MOD)
    options = read_json(root, F_OPT)
    trace = read_json(root, F_TRACE)
    progress = {"status", "completion_evidence", "blockers", "update_notes", "removal_or_deprecation_notes"}
    material = {
        "phases": [_strip(p, progress) for p in plan.get("phases", [])],
        "work_units": [_strip(w, progress) for w in plan.get("work_units", [])],
        "modules": [_strip(m, progress) for m in modules.get("modules", [])],
        "options": [o for o in options.get("options", []) if o.get("status") in {"selected", "deferred"}],
        "requirements": [
            {k: link.get(k) for k in ("requirement_id", "source", "option_ids", "module_ids", "work_unit_ids")}
            for link in trace.get("links", [])
        ],
    }
    preplan = (root / F_PREPLAN).read_text(encoding="utf-8") if (root / F_PREPLAN).exists() else ""
    # Sections filled after approval by design are not part of the approved content:
    # §8 Approved Technology Decisions (Stage 9), §16 Unresolved Human Decisions, §17 Plan Approval, §18 Readiness.
    preplan = re.split(r"(?m)^## 16\.", preplan, maxsplit=1)[0]
    preplan = re.sub(r"(?ms)^## 8\..*?(?=^## 9\.|\Z)", "", preplan)
    preplan = "\n".join(line.rstrip() for line in preplan.strip().splitlines())
    blob = json.dumps(material, sort_keys=True, separators=(",", ":")) + "\n--PRE-PLAN--\n" + preplan
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- consent

def canonical_payload(record: dict[str, Any]) -> str:
    """Identical to upstream scripts/consent_guard.py so records stay compatible."""
    immutable = {
        "id": record.get("id"),
        "type": record.get("type"),
        "title": record.get("title"),
        "summary": record.get("summary"),
        "proposed_changes": record.get("proposed_changes") or [],
        "affected_modules": record.get("affected_modules") or [],
        "risk": record.get("risk"),
        "owner_identity": record.get("owner_identity"),
        "requested_at": record.get("requested_at"),
        "expires_at": record.get("expires_at"),
        "nonce": record.get("nonce"),
    }
    return json.dumps(immutable, sort_keys=True, separators=(",", ":"))


def request_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(record).encode("utf-8")).hexdigest()


def approval_code(record: dict[str, Any]) -> str:
    return str(record.get("nonce", "")).replace("-", "")[:8].upper()


def close_pending(root: Path, ctype: str, reason: str) -> None:
    doc = read_json(root, F_CONSENT)
    changed = False
    for req in doc.get("requests", []):
        if req.get("type") == ctype and req.get("status") == "pending":
            req["status"] = "closed"
            req["implementation_reference"] = f"closed: {reason}"
            changed = True
    if changed:
        write_json(root, F_CONSENT, doc)


def cmd_consent_propose(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    ctype = args.type
    changes = list(args.change or [])
    title = args.title
    summary = args.summary
    if ctype == "plan_approval":
        report = run_validation(root)
        if report["errors"]:
            print_report(report)
            raise AnposError("fix the validation errors above before proposing the plan")
        plan = read_json(root, F_PLAN)
        trace = read_json(root, F_TRACE)
        if not plan.get("phases") or not plan.get("work_units") or not trace.get("links"):
            raise AnposError("a plan needs at least one requirement (traceability link), one phase and one work unit")
        changes = [f"plan_digest:sha256:{plan_digest(root)}"] + changes
        title = title or "Approve Project Plan"
        summary = summary or (f"{len(trace['links'])} requirements, {len(plan['phases'])} phases, "
                              f"{len(plan['work_units'])} work units (docs/ai/PRE-PLAN.md)")
        close_pending(root, "plan_approval", "superseded by a newer plan proposal")
    elif ctype == "technology_stack":
        ok, why = plan_is_approved(root)
        if not ok:
            raise AnposError(f"technology stack consent needs an approved plan first: {why}")
        if not changes:
            raise AnposError("list the proposed stack with --change (one per technology decision)")
        current = read_json(root, F_STATE).get("lifecycle_stage")
        if current == "system_design":
            problems = exit_problems(root, "system_design")
            if problems:
                raise AnposError("finish Stage 8 system design before proposing the stack:\n  - " + "\n  - ".join(problems))
        elif current not in {"technology_consent", "architecture", "data_flow", "ui_ux", "development"}:
            raise AnposError(f"the technology stack is proposed after system design (current stage: {current})")
        title = title or "Approve Technology Stack"
        close_pending(root, "technology_stack", "superseded by a newer stack proposal")
    elif ctype == "release_candidate":
        current = read_json(root, F_STATE).get("lifecycle_stage")
        if current != "release":
            raise AnposError(f"a release candidate is proposed in the release stage (current stage: {current})")
        problems = [p for p in exit_problems(root, "release") if "APPROVE RELEASE" not in p]
        if problems:
            raise AnposError("prepare the release first:\n  - " + "\n  - ".join(problems))
        head = git(root, "rev-parse", "HEAD", check=False)
        if not head:
            raise AnposError("a release candidate must point at a commit; commit the release state first")
        if git(root, "status", "--porcelain", check=False):
            raise AnposError("commit all changes first: a release candidate is tied to one immutable commit")
        changes = [f"release_commit:{head}"] + changes
        title = title or "Approve Release Candidate"
        close_pending(root, "release_candidate", "superseded by a newer release candidate")
    else:
        if not changes:
            raise AnposError("describe the change with --change")
    if not title:
        raise AnposError("--title is required for this consent type")

    doc = read_json(root, F_CONSENT)
    seq = int(doc.get("next_sequence") or 1)
    created = now()
    record = {
        "id": f"CONSENT-{seq:06d}",
        "type": ctype,
        "title": title,
        "summary": summary or "",
        "requested_by": "claude-code:anpos-plugin",
        "requested_at": iso(created),
        "request_hash": None,
        "nonce": str(uuid.uuid4()),
        "expires_at": iso(created + timedelta(hours=max(1, args.expires_hours))),
        "nonce_consumed_at": None,
        "owner_identity": None,
        "canonical_review_reference": F_PREPLAN if ctype == "plan_approval" else None,
        "email_notification_reference": None,
        "proposed_changes": changes,
        "affected_modules": list(args.module or []),
        "risk": args.risk,
        "status": "pending",
        "approved_scope": [],
        "decision_by": None,
        "decision_identity_evidence_ref": None,
        "decision_at": None,
        "decision_evidence": None,
        "decision_request_hash": None,
        "implementation_reference": None,
        "verification_reference": None,
    }
    record["request_hash"] = request_hash(record)
    doc.setdefault("requests", []).append(record)
    doc["next_sequence"] = seq + 1
    write_json(root, F_CONSENT, doc)

    if ctype == "plan_approval":
        plan = read_json(root, F_PLAN)
        plan["plan_status"] = "proposed"
        plan["plan_approval_ref"] = None
        write_json(root, F_PLAN, plan)
        set_stage(root, "plan_proposed")
    elif ctype == "technology_stack":
        previous = read_json(root, F_STATE).get("lifecycle_stage")
        if previous == "system_design":
            log_stage(root, previous, "technology_consent", {"result": "passed"})
            set_stage(root, "technology_consent")

    kind = CONSENT_KIND.get(ctype, "CHANGE")
    code = approval_code(record)
    issue_url = None
    if (root / "config/integrations/vcs-host.json").exists():
        import anpos_govern
        issue_url = anpos_govern.open_host_approval(root, record, f"APPROVE {kind} {code}")
        if issue_url:
            doc = read_json(root, F_CONSENT)
            for req in doc.get("requests", []):
                if req.get("id") == record["id"]:
                    req["canonical_review_reference"] = issue_url  # not part of the hashed payload
            write_json(root, F_CONSENT, doc)
    emit({
        "consent_id": record["id"],
        "type": ctype,
        "approval_code": code,
        "approve_phrase": f"APPROVE {kind} {code}",
        "reject_phrase": f"REJECT {kind} {code}",
        "expires_at": record["expires_at"],
        "approval_issue": issue_url,
        "instruction": (f"Ask a repository admin/maintainer to comment the phrase on {issue_url}, then run `consent confirm`."
                        if issue_url else
                        "Show the user what they are approving, then ask them to type the approve or reject phrase "
                        "themselves. Never type it for them. After they reply, run `consent confirm`."),
    }, True)
    return 0


def set_stage(root: Path, stage: str) -> None:
    state = read_json(root, F_STATE)
    state["lifecycle_stage"] = stage
    write_json(root, F_STATE, state)


def find_token(root: Path, code: str) -> dict[str, Any] | None:
    path = root / APPROVALS_DIR / f"{code}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def cmd_consent_confirm(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    doc = read_json(root, F_CONSENT)
    pending = [r for r in doc.get("requests", []) if r.get("status") == "pending"]
    if args.id:
        pending = [r for r in pending if r.get("id") == args.id]
    if not pending:
        raise AnposError("no pending consent request" + (f" with id {args.id}" if args.id else ""))
    record = pending[-1]
    code = approval_code(record)
    kind = CONSENT_KIND.get(record.get("type"), "CHANGE")

    if parse_time(record["expires_at"]) <= now():
        record["status"] = "expired"
        write_json(root, F_CONSENT, doc)
        raise AnposError(f"{record['id']} expired at {record['expires_at']}. Propose it again.")
    calculated = request_hash(record)
    if calculated != record.get("request_hash"):
        raise AnposError(f"{record['id']} was modified after it was proposed (hash mismatch). Propose it again.")
    if record.get("type") == "plan_approval":
        expected = next((c.split(":", 2)[2] for c in record.get("proposed_changes", []) if c.startswith("plan_digest:sha256:")), None)
        if expected != plan_digest(root):
            raise AnposError("the plan changed after it was proposed. Run `consent propose --type plan_approval` again "
                             "so the user approves the current version.")

    host_channel = False
    if (root / "config/integrations/vcs-host.json").exists():
        host_channel = read_json(root, "config/integrations/vcs-host.json").get("approval_channel") == "host_comment"
    decided = now()
    if host_channel and record.get("canonical_review_reference", "").startswith("http"):
        import anpos_govern
        hd = anpos_govern.host_decision(root, record, kind, code)
        if not hd:
            raise AnposError(f"no approve/reject comment from a repository admin/maintainer yet on "
                             f"{record['canonical_review_reference']} (expected `APPROVE {kind} {code}`)")
        decision = "approved" if hd["decision"] == "approve" else "rejected"
        record["decision_by"] = f"{hd['host']}:{hd['login']}"
        record["decision_identity_evidence_ref"] = hd["evidence"]
        record["decision_evidence"] = (f"{hd['host']} user {hd['login']} ({hd['permission']}) commented "
                                       f"`{hd['decision'].upper()} {kind} {code}` at {hd['at']}; identity authenticated by "
                                       f"{hd['host']} and permission re-read through its API.")
    else:
        token = find_token(root, code)
        if not token:
            raise AnposError(
                f"no user decision captured for code {code}. The user must type `APPROVE {kind} {code}` or "
                f"`REJECT {kind} {code}` in the chat themselves. (This is captured by the plugin's UserPromptSubmit hook; "
                "if the user already typed it, check that the anpos plugin hooks are enabled.)")
        if token.get("kind") != kind or token.get("code") != code:
            raise AnposError("captured decision does not match this request")
        if token.get("consumed_at"):
            raise AnposError("this decision token was already used")
        captured = parse_time(token["captured_at"])
        if captured < parse_time(record["requested_at"]) or captured > parse_time(record["expires_at"]):
            raise AnposError("the captured decision is outside the request's validity window")
        decision = "approved" if token.get("decision") == "approve" else "rejected"
        token_rel = f"{APPROVALS_DIR}/{code}.json"
        record["decision_by"] = f"interactive-user:claude-code-session:{token.get('session_id') or 'unknown'}"
        record["decision_identity_evidence_ref"] = token_rel
        record["decision_evidence"] = (f"User typed the exact phrase `{token.get('decision', '').upper()} {kind} {code}`; "
                                       f"captured by the UserPromptSubmit hook at {token['captured_at']} "
                                       f"(prompt sha256 {token.get('prompt_sha256')}). Local session evidence, not an authenticated identity.")
        token["consumed_at"] = iso(decided)
        token["consumed_by"] = record["id"]
        write_json(root, token_rel, token)

    record["status"] = decision
    record["approved_scope"] = list(record.get("proposed_changes", [])) if decision == "approved" else []
    record["decision_at"] = iso(decided)
    record["decision_request_hash"] = calculated
    record["nonce_consumed_at"] = iso(decided)
    write_json(root, F_CONSENT, doc)
    audit(root, "consent_decision", record["id"], decision, record["id"], actor=record["decision_by"], role="owner",
          risk="elevated" if record.get("type") != "plan_approval" else "normal")

    if record.get("type") == "plan_approval":
        plan = read_json(root, F_PLAN)
        if decision == "approved":
            plan["plan_status"] = "approved"
            plan["plan_approval_ref"] = record["id"]
            set_stage(root, "plan_approved")
        else:
            plan["plan_status"] = "draft"
            plan["plan_approval_ref"] = None
            set_stage(root, "planning")
        write_json(root, F_PLAN, plan)
    elif record.get("type") == "technology_stack":
        current = read_json(root, F_STATE).get("lifecycle_stage")
        if current == "technology_consent":
            target = "architecture" if decision == "approved" else "system_design"
            log_stage(root, current, target, {"result": f"consent {record['id']} {decision}"})
            set_stage(root, target)
    elif record.get("type") == "scope_change" and decision == "approved":
        import anpos_team
        applied = anpos_team.apply_consented_actions(root, record)
        if applied:
            record_doc = read_json(root, F_CONSENT)
            for req in record_doc.get("requests", []):
                if req.get("id") == record["id"]:
                    req["status"] = "implemented"
                    req["implementation_reference"] = "applied by anpos: " + ", ".join(applied)
            write_json(root, F_CONSENT, record_doc)
    elif record.get("type") == "release_candidate":
        current = read_json(root, F_STATE).get("lifecycle_stage")
        if current == "release" and decision == "approved":
            log_stage(root, current, "operations", {"result": f"consent {record['id']} approved"})
            set_stage(root, "operations")
    print(f"{record['id']} ({record['type']}): {decision}")
    return 0


# ---------------------------------------------------------------- validation

def _cycles(nodes: dict[str, list[str]]) -> list[list[str]]:
    color: dict[str, int] = {}
    found: list[list[str]] = []
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dep in nodes.get(node, []):
            if dep not in nodes:
                continue
            if color.get(dep) == 1:
                found.append(stack[stack.index(dep):] + [dep])
            elif color.get(dep) is None:
                visit(dep)
        stack.pop()
        color[node] = 2

    for n in nodes:
        if color.get(n) is None:
            visit(n)
    return found


def run_validation(root: Path) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    docs: dict[str, dict[str, Any]] = {}

    for rel in PLANNER_FILES:
        if not (root / rel).exists():
            errors.append(f"missing {rel}")
    for rel in SCHEMA_FOR:
        if (root / rel).exists():
            try:
                docs[rel] = json.loads((root / rel).read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{rel}: invalid JSON ({exc})")
    if errors:
        return {"errors": errors, "warnings": warnings}

    try:
        from jsonschema import Draft202012Validator  # type: ignore
    except ImportError:
        Draft202012Validator = None
        warnings.append("python package `jsonschema` not installed: full schema validation skipped "
                        "(pip install jsonschema). Cross-reference checks still ran.")
    if Draft202012Validator is not None:
        for rel, schema_name in SCHEMA_FOR.items():
            schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
            for err in sorted(Draft202012Validator(schema).iter_errors(docs[rel]), key=lambda e: list(e.path)):
                where = "/".join(str(p) for p in err.path) or "<root>"
                errors.append(f"{rel} [{where}]: {err.message}")

    plan, mods, opts = docs[F_PLAN], docs[F_MOD], docs[F_OPT]
    trace, evid, consents, state = docs[F_TRACE], docs[F_EVID], docs[F_CONSENT], docs[F_STATE]

    def ids(items: list[Any], label: str, key: str = "id") -> set[str]:
        seen: set[str] = set()
        for item in items:
            if isinstance(item, dict) and item.get(key):
                if item[key] in seen:
                    errors.append(f"duplicate {label} id {item[key]}")
                seen.add(str(item[key]))
        return seen

    opt_ids = ids(opts.get("options", []), "option")
    mod_ids = ids(mods.get("modules", []), "module")
    phase_ids = ids(plan.get("phases", []), "phase")
    wu_ids = ids(plan.get("work_units", []), "work unit")
    req_ids = ids(trace.get("links", []), "requirement", "requirement_id")
    evid_ids = ids(evid.get("entries", []), "evidence")
    ids(consents.get("requests", []), "consent request")

    def refs(values: Any, known: set[str], where: str, soft: bool = False) -> None:
        for value in values or []:
            if value not in known:
                (warnings if soft else errors).append(f"{where}: unknown reference {value}")

    for m in mods.get("modules", []):
        refs(m.get("option_ids"), opt_ids, f"module {m.get('id')} option_ids")
        refs(m.get("dependencies"), mod_ids, f"module {m.get('id')} dependencies")
    for p in plan.get("phases", []):
        refs(p.get("module_ids"), mod_ids, f"phase {p.get('id')} module_ids")
        refs(p.get("dependencies"), phase_ids, f"phase {p.get('id')} dependencies")
    for w in plan.get("work_units", []):
        wid = w.get("id")
        refs([w.get("phase_id")] if w.get("phase_id") else [], phase_ids, f"work unit {wid} phase_id")
        refs([w.get("module_id")] if w.get("module_id") else [], mod_ids, f"work unit {wid} module_id")
        refs(w.get("dependencies"), wu_ids, f"work unit {wid} dependencies")
        refs(w.get("requirement_ids"), req_ids, f"work unit {wid} requirement_ids")
        if not w.get("acceptance_criteria"):
            warnings.append(f"work unit {wid} has no acceptance_criteria")
        if not w.get("requirement_ids"):
            warnings.append(f"work unit {wid} is not linked to any requirement")
    for o in opts.get("options", []):
        refs(o.get("dependencies"), opt_ids, f"option {o.get('id')} dependencies")
        refs(o.get("conflicts"), opt_ids, f"option {o.get('id')} conflicts")
    for link in trace.get("links", []):
        rid = link.get("requirement_id")
        refs(link.get("option_ids"), opt_ids, f"requirement {rid} option_ids")
        refs(link.get("module_ids"), mod_ids, f"requirement {rid} module_ids")
        refs(link.get("work_unit_ids"), wu_ids, f"requirement {rid} work_unit_ids")
        if not link.get("source"):
            warnings.append(f"requirement {rid} has no source (user statement or EVID-* id)")
        elif re.fullmatch(r"EVID-\d{6}", str(link.get("source"))) and link["source"] not in evid_ids:
            errors.append(f"requirement {rid} source {link['source']} is not in the evidence registry")
    for e in evid.get("entries", []):
        refs(e.get("supports_requirement_ids"), req_ids, f"evidence {e.get('id')} supports_requirement_ids", soft=True)
        refs(e.get("supports_option_ids"), opt_ids, f"evidence {e.get('id')} supports_option_ids", soft=True)
        refs(e.get("supports_module_ids"), mod_ids, f"evidence {e.get('id')} supports_module_ids", soft=True)
        refs(e.get("supersedes"), evid_ids, f"evidence {e.get('id')} supersedes")
        if e.get("source_type") in {"primary", "secondary"} and not e.get("accessed_at"):
            warnings.append(f"evidence {e.get('id')} has no accessed_at date")

    for label, graph in (
        ("work unit", {w["id"]: list(w.get("dependencies", [])) for w in plan.get("work_units", []) if w.get("id")}),
        ("module", {m["id"]: list(m.get("dependencies", [])) for m in mods.get("modules", []) if m.get("id")}),
        ("phase", {p["id"]: list(p.get("dependencies", [])) for p in plan.get("phases", []) if p.get("id")}),
    ):
        for cycle in _cycles(graph):
            errors.append(f"{label} dependency cycle: {' -> '.join(cycle)}")

    phase_order = {p.get("id"): i for i, p in enumerate(plan.get("phases", []))}
    wu_phase = {w.get("id"): w.get("phase_id") for w in plan.get("work_units", [])}
    for w in plan.get("work_units", []):
        for dep in w.get("dependencies", []):
            if phase_order.get(wu_phase.get(dep), -1) > phase_order.get(w.get("phase_id"), -1):
                warnings.append(f"work unit {w.get('id')} depends on {dep}, which is in a later phase")
    if plan.get("work_units"):
        covered = {r for w in plan["work_units"] for r in w.get("requirement_ids", [])}
        for rid in sorted(req_ids - covered):
            warnings.append(f"requirement {rid} has no work unit")
        planned_modules = {m for p in plan.get("phases", []) for m in p.get("module_ids", [])}
        for mid in sorted(mod_ids - planned_modules):
            warnings.append(f"module {mid} is not assigned to any phase")

    for req in consents.get("requests", []):
        if req.get("request_hash") and request_hash(req) != req.get("request_hash"):
            errors.append(f"consent {req.get('id')} content does not match its request_hash (tampered or edited)")
        if req.get("status") in {"approved", "partially_approved", "rejected"}:
            if req.get("decision_request_hash") != req.get("request_hash"):
                errors.append(f"consent {req.get('id')} decision_request_hash does not match request_hash")

    status = plan.get("plan_status")
    ref = plan.get("plan_approval_ref")
    if status == "approved":
        match = next((r for r in consents.get("requests", []) if r.get("id") == ref), None)
        if not match or match.get("type") != "plan_approval" or match.get("status") not in APPROVED_STATES:
            errors.append("plan_status is approved but plan_approval_ref does not name an approved plan_approval consent")
        else:
            expected = next((c.split(":", 2)[2] for c in match.get("proposed_changes", [])
                             if str(c).startswith("plan_digest:sha256:")), None)
            if expected and expected != plan_digest(root):
                warnings.append("the plan content changed after approval. If the change is material (scope, modules, "
                                "critical path) run `stage planning --revise` and get a new approval.")
    elif ref:
        errors.append(f"plan_approval_ref is set ({ref}) but plan_status is {status}")

    stage = state.get("lifecycle_stage")
    if stage in STAGE_INDEX and STAGE_INDEX[stage] >= STAGE_INDEX["plan_approved"] and status != "approved":
        errors.append(f"lifecycle_stage is {stage} but the plan is not approved")
    if stage in STAGE_INDEX and STAGE_INDEX[stage] > STAGE_INDEX["technology_consent"]:
        if not any(r.get("type") == "technology_stack" and r.get("status") in APPROVED_STATES
                   for r in consents.get("requests", [])):
            errors.append(f"lifecycle_stage is {stage} but no technology_stack consent is approved")
    for key, known in (("current_work_unit", wu_ids), ("next_valid_work_unit", wu_ids),
                       ("current_module", mod_ids), ("current_phase", phase_ids)):
        if state.get(key) and state[key] not in known:
            errors.append(f"project-state {key} {state[key]} does not exist")
    total = len(plan.get("work_units", []))
    if status == "approved" and (state.get("progress") or {}).get("total_work_units", 0) != total:
        warnings.append("project-state progress is out of date (run `sync-state`)")
    if is_full_child(root):
        protected = []
        try:
            protected = json.loads((root / "config/security/control-plane-policy.json").read_text()).get("protected_paths") or []
        except (OSError, json.JSONDecodeError):
            pass

        def prefix(p: str) -> str:
            p = p.lstrip("/")
            for m in ("*", "?", "["):
                p = p.split(m, 1)[0]
            return p.rstrip("/")

        for w in plan.get("work_units", []):
            for ap in w.get("allowed_paths") or []:
                a = prefix(str(ap))
                hit = next((p for p in protected if not a or not prefix(p) or a == prefix(p)
                            or a.startswith(prefix(p) + "/") or prefix(p).startswith(a + "/")), None)
                if hit:
                    warnings.append(f"work unit {w.get('id')} allowed path {ap} overlaps protected ANPOS path {hit}: "
                                    "workers can't be dispatched to it (use project paths such as src/**)")
                    break
    upstream_validation(root, errors, warnings)
    return {"errors": errors, "warnings": warnings}


def read_marker(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / F_MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_full_child(root: Path) -> bool:
    try:
        return json.loads((root / "config/protocol/instance.json").read_text()).get("instance_status") == "active_project"
    except (OSError, json.JSONDecodeError):
        return False


def upstream_validation(root: Path, errors: list[str], warnings: list[str]) -> None:
    """In a full ANPOS child, also run the project's own validate_ai_native_repo.py."""
    script = root / "scripts/validate_ai_native_repo.py"
    if not script.exists() or not is_full_child(root):
        return
    try:
        proc = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"upstream ANPOS validator could not run: {exc}")
        return
    if proc.returncode == 0:
        return
    out = proc.stdout + proc.stderr
    if "No module named 'jsonschema'" in out:
        warnings.append("upstream ANPOS validator needs `pip install jsonschema` (see requirements-anpos.txt)")
        return
    marker = read_marker(root)
    kept = set(marker.get("kept_for_review") or [])
    project_workflows = marker.get("project_workflows") or []
    lines = [line.strip()[2:] for line in out.splitlines() if line.strip().startswith("- ")]
    if not lines:
        errors.append("upstream ANPOS validator failed: " + out.strip()[-500:])
    for msg in lines:
        follow_up = ("CODEOWNERS" in msg and ".github/CODEOWNERS" in kept) or \
                    any(f".github/workflows/{w}" in msg for w in project_workflows)
        if follow_up:
            warnings.append(f"adoption follow-up (upstream validator): {msg}")
        else:
            errors.append(f"upstream validator: {msg}")


def print_report(report: dict[str, list[str]]) -> None:
    for e in report["errors"]:
        print(f"ERROR   {e}")
    for w in report["warnings"]:
        print(f"WARN    {w}")
    print(f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")


def cmd_validate(args: argparse.Namespace) -> int:
    report = run_validation(find_root(args.root))
    if args.json:
        emit(report, True)
    else:
        print_report(report)
    return 1 if report["errors"] else 0


# ---------------------------------------------------------------- status / next / sync

def ready_units(plan: dict[str, Any]) -> list[dict[str, Any]]:
    units = plan.get("work_units", [])
    done = {w["id"] for w in units if w.get("status") in DONE_WU}
    phase_order = {p.get("id"): i for i, p in enumerate(plan.get("phases", []))}
    ready = [w for w in units
             if w.get("status") in {"not_started", "ready"} and all(d in done for d in w.get("dependencies", []))]
    return sorted(ready, key=lambda w: (phase_order.get(w.get("phase_id"), 999), -int(w.get("priority") or 0), w["id"]))


NEXT_STEP = {
    "not_started": "Run /anpos:start to install the planner.",
    "initialization": "Collect the free-form intake (/anpos:intake).",
    "intake": "Normalize the intake, then start research (/anpos:research).",
    "research": "Finish research and record EVID-* entries, then plan (/anpos:plan).",
    "planning": "Build the plan files, validate, then propose it for approval (/anpos:plan).",
    "plan_proposed": "Waiting for the user to type the APPROVE PLAN phrase, then run `consent confirm`.",
    "plan_approved": "Plan approved. Continue with Stage 8 System Design (/anpos:lifecycle).",
    "system_design": "Finish system design, then propose the technology stack (Stage 9, /anpos:lifecycle).",
    "technology_consent": "Waiting for the user to type the APPROVE STACK phrase, then run `consent confirm`.",
    "release": "Prepare RELEASE.md and environments, commit, then propose the release candidate (APPROVE RELEASE).",
    "maintenance": "Maintenance: watch technology updates, take new scope back to planning (`stage planning --revise`).",
}


def compute_progress(plan: dict[str, Any]) -> dict[str, int]:
    units = plan.get("work_units", [])
    count = lambda *s: sum(1 for w in units if w.get("status") in s)  # noqa: E731
    return {
        "total_work_units": len(units),
        "complete": count("complete"),
        "in_progress": count("in_progress", "verification_required"),
        "blocked": count("blocked"),
        "deferred": count("deferred"),
        "needs_update": count("needs_update"),
        "needs_removal": count("needs_removal"),
    }


def cmd_status(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    info = detect(root)
    if info["state"] not in {"planner_lite", "active_project"} or info["planner_files_missing"] == PLANNER_FILES:
        if args.brief:
            return 0
        print(f"No ANPOS planner here (state: {info['state']}). {info['recommended_action']}")
        return 0
    try:
        state = read_json(root, F_STATE)
        plan = read_json(root, F_PLAN)
        consents = read_json(root, F_CONSENT)
        evid = read_json(root, F_EVID)
        trace = read_json(root, F_TRACE)
        mods = read_json(root, F_MOD)
        opts = read_json(root, F_OPT)
    except AnposError as exc:
        print(f"ANPOS planner present but unreadable: {exc}")
        return 1
    stage = state.get("lifecycle_stage", "not_started")
    pending = [r for r in consents.get("requests", []) if r.get("status") == "pending"]
    ready = ready_units(plan)
    prog = compute_progress(plan)
    if args.brief:
        line = (f"ANPOS project detected ({info['state']}): stage={stage}, plan={plan.get('plan_status')}, "
                f"work units {prog['complete']}/{prog['total_work_units']} complete.")
        if pending:
            p = pending[-1]
            line += f" Pending consent {p['id']} ({p['type']}), code {approval_code(p)}."
        line += " Use the anpos skills (/anpos:status) to continue."
        print(line)
        return 0
    selected = sum(1 for o in opts.get("options", []) if o.get("status") == "selected")
    out = [
        f"ANPOS planner — {root}",
        f"  mode            {'full ANPOS child' if info['state'] == 'active_project' else 'planner lite'}",
        f"  lifecycle_stage {stage}",
        f"  plan_status     {plan.get('plan_status')}  (approval: {plan.get('plan_approval_ref') or '-'})",
        f"  evidence        {len(evid.get('entries', []))} EVID entries",
        f"  requirements    {len(trace.get('links', []))}",
        f"  options         {len(opts.get('options', []))} ({selected} selected)",
        f"  modules         {len(mods.get('modules', []))}",
        f"  phases          {len(plan.get('phases', []))}",
        f"  work units      {prog['total_work_units']} total, {prog['complete']} complete, "
        f"{prog['in_progress']} in progress, {prog['blocked']} blocked",
    ]
    for p in pending:
        kind = CONSENT_KIND.get(p.get("type"), "CHANGE")
        out.append(f"  PENDING         {p['id']} {p['type']}: user must type `APPROVE {kind} {approval_code(p)}` "
                   f"(expires {p['expires_at']})")
    pm_state = pm_load(root)
    if pm_state.get("provider_id") or pm_state.get("mode") == "skipped":
        open_conflicts = sum(1 for c in pm_state.get("conflicts", []) if not c.get("resolved_at"))
        out.append(f"  project mgmt    {pm_state.get('provider_name') or 'skipped'}"
                   + (f" · {len(pm_state.get('items', {}))} items mapped · last sync {pm_state.get('last_sync_at') or '-'}"
                      if pm_state.get("provider_id") else "")
                   + (f" · {open_conflicts} open conflict(s)" if open_conflicts else ""))
    elif plan.get("plan_status") == "approved":
        out.append("  project mgmt    not chosen (/anpos:pm)")
    if ready and plan.get("plan_status") == "approved":
        out.append("  ready next:")
        for w in ready[:5]:
            out.append(f"    {w['id']}  [{w.get('phase_id')}/{w.get('module_id')}] {w.get('title')}")
    if stage in STAGE_INDEX and STAGE_INDEX[stage] >= STAGE_INDEX["system_design"] and stage != "maintenance":
        blockers = exit_problems(root, stage)
        out.append(f"  exit check      {'ready to move on' if not blockers else f'{len(blockers)} item(s) left (stage-check)'}")
    next_step = NEXT_STEP.get(stage, "Continue the current lifecycle stage (/anpos:lifecycle).")
    if stage == "technology_consent" and stack_is_approved(root) and not any(p.get("type") == "technology_stack" for p in pending):
        next_step = "Stack approved. Move on with `stage architecture` (/anpos:lifecycle)."
    out.append(f"  next step       {next_step}")
    print("\n".join(out))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    ok, why = plan_is_approved(root)
    if not ok:
        raise AnposError(f"no executable work yet: {why}")
    ready = ready_units(read_json(root, F_PLAN))
    if args.json:
        emit([{k: w.get(k) for k in ("id", "phase_id", "module_id", "title", "priority", "acceptance_criteria",
                                     "required_checks", "allowed_paths")} for w in ready[: args.limit]], True)
    else:
        if not ready:
            print("No ready work units (all done, or remaining ones are blocked by dependencies).")
        for w in ready[: args.limit]:
            print(f"{w['id']}  [{w.get('phase_id')}/{w.get('module_id')}] p{w.get('priority')}  {w.get('title')}")
    return 0


def cmd_sync_state(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    plan = read_json(root, F_PLAN)
    state = read_json(root, F_STATE)
    state["progress"] = compute_progress(plan)
    ready = ready_units(plan) if plan.get("plan_status") == "approved" else []
    state["next_valid_work_unit"] = ready[0]["id"] if ready else None
    active = next((w for w in plan.get("work_units", []) if w.get("status") == "in_progress"), None)
    state["current_work_unit"] = active["id"] if active else None
    state["current_module"] = active.get("module_id") if active else None
    state["current_phase"] = active.get("phase_id") if active else (ready[0].get("phase_id") if ready else None)
    state["last_reconciled_at"] = iso(now())
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        state["last_reconciled_repository_ref"] = head or None
    except (OSError, subprocess.CalledProcessError):
        pass
    write_json(root, F_STATE, state)
    print(json.dumps(state["progress"]), "next:", state["next_valid_work_unit"])
    return 0


def cmd_wu(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    ok, why = plan_is_approved(root)
    if not ok:
        raise AnposError(f"work units can only be executed from an approved plan: {why}")
    plan = read_json(root, F_PLAN)
    unit = next((w for w in plan.get("work_units", []) if w.get("id") == args.id), None)
    if not unit:
        raise AnposError(f"unknown work unit {args.id}")
    if args.status == "complete" and not args.evidence and not unit.get("completion_evidence"):
        raise AnposError("marking a work unit complete needs --evidence (test run, commit, PR, screenshot...)")
    if args.status in {"in_progress", "complete"}:
        done = {w["id"] for w in plan["work_units"] if w.get("status") in DONE_WU}
        open_deps = [d for d in unit.get("dependencies", []) if d not in done]
        if open_deps:
            raise AnposError(f"{args.id} still depends on unfinished work units: {', '.join(open_deps)}")
    unit["status"] = args.status
    if args.evidence:
        unit.setdefault("completion_evidence", []).extend(args.evidence)
    if args.blocker:
        unit.setdefault("blockers", []).extend(args.blocker)
    write_json(root, F_PLAN, plan)
    cmd_sync_state(args)
    return 0


# ---------------------------------------------------------------- export

def _wu_body(unit: dict[str, Any], plan_label: str) -> str:
    def bullets(items: list[Any]) -> str:
        return "\n".join(f"- [ ] {i}" for i in items) if items else "- (none)"
    lines = [
        f"**Objective:** {unit.get('objective') or '-'}",
        "",
        f"**Phase:** {unit.get('phase_id')} · **Module:** {unit.get('module_id')} · **Priority:** {unit.get('priority')} "
        f"· **Risk:** {unit.get('risk_classification')}",
        f"**Requirements:** {', '.join(unit.get('requirement_ids') or []) or '-'}",
        f"**Depends on:** {', '.join(unit.get('dependencies') or []) or '-'}",
        "",
        "**Acceptance criteria**",
        bullets(unit.get("acceptance_criteria") or []),
        "",
        "**Required checks**",
        bullets(unit.get("required_checks") or []),
        "",
        f"**Allowed paths:** {', '.join(f'`{p}`' for p in unit.get('allowed_paths') or []) or '-'}",
        "",
        f"_Source: ANPOS execution plan ({plan_label}), work unit `{unit.get('id')}`._",
    ]
    return "\n".join(lines)


def cmd_export(args: argparse.Namespace) -> int:
    import csv
    import io

    root = find_root(args.root)
    plan = read_json(root, F_PLAN)
    mods = {m.get("id"): m for m in read_json(root, F_MOD).get("modules", [])}
    approved, _ = plan_is_approved(root)
    label = f"approved via {plan.get('plan_approval_ref')}" if approved else f"{plan.get('plan_status')} — NOT APPROVED"
    units = plan.get("work_units", [])
    if args.only_open:
        units = [w for w in units if w.get("status") not in DONE_WU]

    if args.format == "md":
        out = [f"# Project plan ({label})", ""]
        for phase in plan.get("phases", []):
            out += [f"## {phase.get('id')} — {phase.get('name')}", "", phase.get("objective") or "", ""]
            if phase.get("exit_criteria"):
                out += ["Exit criteria: " + "; ".join(phase["exit_criteria"]), ""]
            for unit in [w for w in units if w.get("phase_id") == phase.get("id")]:
                module = mods.get(unit.get("module_id"), {})
                out += [f"### {unit.get('id')} — {unit.get('title')}  `[{unit.get('status')}]`",
                        f"_Module: {module.get('name', unit.get('module_id'))}_", "", _wu_body(unit, label), ""]
        text = "\n".join(out).rstrip() + "\n"
    elif args.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["ID", "Title", "Description", "Phase", "Module", "Requirements", "Depends On", "Priority",
                         "Status", "Risk", "Acceptance Criteria", "Required Checks", "Labels"])
        for unit in units:
            writer.writerow([
                unit.get("id"), unit.get("title"), unit.get("objective"), unit.get("phase_id"),
                mods.get(unit.get("module_id"), {}).get("name", unit.get("module_id")),
                ";".join(unit.get("requirement_ids") or []), ";".join(unit.get("dependencies") or []),
                unit.get("priority"), unit.get("status"), unit.get("risk_classification"),
                " | ".join(unit.get("acceptance_criteria") or []), " | ".join(unit.get("required_checks") or []),
                ";".join(["anpos", str(unit.get("phase_id")), str(unit.get("module_id"))]),
            ])
        text = buf.getvalue()
    else:  # issues
        text = json.dumps([
            {"work_unit_id": unit.get("id"), "title": f"[{unit.get('id')}] {unit.get('title')}",
             "body": _wu_body(unit, label), "labels": ["anpos", str(unit.get("phase_id")), str(unit.get("module_id"))]}
            for unit in units
        ], indent=2, ensure_ascii=False) + "\n"

    if args.output:
        target = (root / args.output) if not Path(args.output).is_absolute() else Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(units)} work units, plan {label})")
    else:
        sys.stdout.write(text)
    return 0


# ---------------------------------------------------------------- project management sync

F_PMSTATE = "config/integrations/pm-sync-state.json"
F_PM = "config/integrations/project-management.json"
F_LINEAR = "config/integrations/linear-sync.json"
BUILTIN_PROVIDERS = [
    ("linear", "Linear"), ("github_projects", "GitHub Projects / Issues"), ("jira", "Jira / Atlassian"),
    ("clickup", "ClickUp"), ("gitlab", "GitLab Issues / Boards"), ("azure_devops", "Azure DevOps Boards"),
    ("plane", "Plane"), ("asana", "Asana"), ("monday", "monday.com"), ("notion", "Notion"),
]
GH_PROVIDER = "github_projects"


def pm_load(root: Path) -> dict[str, Any]:
    if (root / F_PMSTATE).exists():
        return read_json(root, F_PMSTATE)
    return {
        "schema_version": 1, "managed_by": "anpos plugin (scripts/anpos.py pm …)",
        "authority": "config/integrations/sync-authority.json (repository is canonical for implementation reality)",
        "provider_id": None, "provider_name": None, "mode": None,
        "workspace": {"id": None, "name": None}, "project": {"id": None, "name": None, "url": None},
        "selected_at": None, "verified_at": None, "sync_enabled": False,
        "items": {}, "conflicts": [], "next_conflict": 1,
        "last_sync_at": None, "last_pull_at": None, "last_error": None,
    }


def provider_catalog(root: Path) -> list[tuple[str, str]]:
    if (root / F_PM).exists():
        doc = read_json(root, F_PM)
        listed = [(p.get("id"), p.get("name")) for p in doc.get("providers", []) if p.get("id")]
        if listed:
            return listed
    return BUILTIN_PROVIDERS


def gh_ready() -> tuple[bool, str]:
    if not shutil.which("gh"):
        return False, "GitHub CLI `gh` is not installed"
    proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    return (proc.returncode == 0, "gh is logged in" if proc.returncode == 0 else "run `gh auth login`")


def cmd_pm_providers(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    ok, detail = gh_ready()
    rows = []
    for pid, name in provider_catalog(root):
        if pid == GH_PROVIDER:
            how = f"git_native via gh — {'ready' if ok else detail}"
        else:
            how = "via a Claude Code connector/MCP for this tool (check the session's tools), or export CSV"
        rows.append({"id": pid, "name": name, "recommended": pid == "linear", "connection": how,
                     "selected": pid == state.get("provider_id")})
    emit({"providers": rows, "current": {"provider_id": state.get("provider_id"), "mode": state.get("mode")}}, True)
    return 0


def _mirror_selection(root: Path, state: dict[str, Any]) -> None:
    """Keep the upstream ANPOS selection files (full mode) in step with the plugin's sync state."""
    if (root / F_PM).exists():
        pm = read_json(root, F_PM)
        sel = pm.setdefault("selection", {})
        skipped = state.get("mode") == "skipped"
        sel.update({
            "status": "skipped" if skipped else ("selected" if state.get("provider_id") else "not_selected"),
            "selected_provider_id": state.get("provider_id"), "selected_provider_name": state.get("provider_name"),
            "workspace_or_org_id": state["workspace"].get("id"), "workspace_or_org_name": state["workspace"].get("name"),
            "external_project_id": state["project"].get("id"), "external_project_name": state["project"].get("name"),
            "external_project_url": state["project"].get("url"), "connected_at": state.get("selected_at"),
            "verified_at": state.get("verified_at"), "sync_enabled": bool(state.get("sync_enabled")),
        })
        pm["status"] = "skipped" if skipped else ("provider_selected" if state.get("provider_id") else pm.get("status"))
        write_json(root, F_PM, pm)
    if (root / F_LINEAR).exists():
        lin = read_json(root, F_LINEAR)
        active = state.get("provider_id") == "linear"
        lin["enabled"] = bool(active and state.get("sync_enabled"))
        lin["status"] = "selected" if active else "not_selected"
        lin["project"] = {k: (state["project"].get(k) if active else None) for k in ("name", "id", "url")}
        lin["sync_result"] = ("selected_pending_first_sync" if active else "provider_not_selected")
        write_json(root, F_LINEAR, lin)


def cmd_pm_select(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    catalog = dict(provider_catalog(root))
    if args.provider not in catalog:
        raise AnposError(f"unknown provider {args.provider}. Known: {', '.join(catalog)}")
    state = pm_load(root)
    if state.get("provider_id") and state["provider_id"] != args.provider and state.get("items"):
        if not args.switch:
            raise AnposError(f"already syncing with {state['provider_id']} ({len(state['items'])} mapped items). "
                             "Switching providers: run `pm pull` first to reconcile, then re-run with --switch "
                             "(external id history is preserved).")
        state.setdefault("previous_providers", []).append({
            "provider_id": state["provider_id"], "items": state["items"], "switched_at": iso(now())})
        state["items"] = {}
    mode = args.mode or ("git_native" if args.provider == GH_PROVIDER else "mcp")
    verified = args.verified
    if args.provider == GH_PROVIDER:
        ok, detail = gh_ready()
        slug = git_remote_slug(root)
        if not ok or not slug:
            raise AnposError(f"GitHub sync needs gh login and a github remote ({detail}; remote: {slug})")
        verified = True
        state["workspace"] = {"id": slug.split("/")[0], "name": slug.split("/")[0]}
        state["project"] = {"id": slug, "name": slug, "url": args.project_url or f"https://github.com/{slug}"}
    else:
        state["workspace"] = {"id": args.workspace_id, "name": args.workspace_name}
        state["project"] = {"id": args.project_id, "name": args.project_name, "url": args.project_url}
    state.update({
        "provider_id": args.provider, "provider_name": catalog[args.provider], "mode": mode,
        "selected_at": iso(now()), "verified_at": iso(now()) if verified else None, "sync_enabled": bool(verified),
    })
    write_json(root, F_PMSTATE, state)
    _mirror_selection(root, state)
    print(f"PM provider: {catalog[args.provider]} ({mode}); sync {'enabled' if verified else 'pending verification (--verified once the project mapping is confirmed)'}")
    return 0


def cmd_pm_skip(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    state.update({"provider_id": None, "provider_name": None, "mode": "skipped", "sync_enabled": False,
                  "selected_at": iso(now()), "verified_at": None})
    write_json(root, F_PMSTATE, state)
    _mirror_selection(root, state)
    print("Project management skipped. The repository plan stays the only source; connect a provider any time with `pm select`.")
    return 0


def _sync_key(root: Path, item_id: str) -> str:
    try:
        instance = json.loads((root / "config/protocol/instance.json").read_text()).get("instance_id")
    except (OSError, json.JSONDecodeError):
        instance = None
    base = instance or git_remote_slug(root) or root.name
    return f"anpos:{base}:{item_id}"


def desired_items(root: Path) -> list[dict[str, Any]]:
    plan = read_json(root, F_PLAN)
    approved, _ = plan_is_approved(root)
    label = f"approved via {plan.get('plan_approval_ref')}" if approved else f"{plan.get('plan_status')} — NOT APPROVED"
    phase_titles = {p.get("id"): f"{p.get('id')} — {p.get('name')}" for p in plan.get("phases", [])}
    items = []
    for p in plan.get("phases", []):
        key = _sync_key(root, p["id"])
        body = (f"{p.get('objective') or ''}\n\n**Exit criteria**\n" +
                "\n".join(f"- [ ] {c}" for c in p.get("exit_criteria") or []) + f"\n\n<!-- {key} -->")
        items.append({"id": p["id"], "kind": "phase", "title": phase_titles[p["id"]], "body": body,
                      "state": "closed" if p.get("status") == "complete" else "open", "idempotency_key": key})
    for w in plan.get("work_units", []):
        key = _sync_key(root, w["id"])
        items.append({"id": w["id"], "kind": "work_unit", "title": f"[{w['id']}] {w.get('title')}",
                      "body": _wu_body(w, label) + f"\n\n<!-- {key} -->",
                      "state": "closed" if w.get("status") in DONE_WU else "open",
                      "phase_title": phase_titles.get(w.get("phase_id")), "priority": w.get("priority"),
                      "labels": ["anpos"], "idempotency_key": key, "repo_status": w.get("status")})
    for it in items:
        it["digest"] = hashlib.sha256(json.dumps([it["title"], it["body"], it["state"], it.get("phase_title")],
                                                 ensure_ascii=False).encode()).hexdigest()
    return items


def pm_actions(root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    wanted = desired_items(root)
    ids = set()
    for it in wanted:
        ids.add(it["id"])
        mapped = state["items"].get(it["id"])
        if not mapped or not mapped.get("external_id"):
            actions.append(dict(it, action="create"))
        elif mapped.get("pushed_digest") != it["digest"]:
            actions.append(dict(it, action="update", external_id=mapped["external_id"], url=mapped.get("url")))
    for item_id, mapped in state["items"].items():
        if item_id not in ids and not mapped.get("archived"):
            actions.append({"action": "archive", "id": item_id, "kind": mapped.get("kind"),
                            "external_id": mapped.get("external_id"), "url": mapped.get("url")})
    return actions


def _require_provider(state: dict[str, Any]) -> None:
    if not state.get("provider_id"):
        raise AnposError("no project management provider selected (run `pm providers`, then `pm select <id>` or `pm skip`)")


def cmd_pm_plan(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    _require_provider(state)
    approved, why = plan_is_approved(root)
    if not approved and not args.include_draft:
        raise AnposError(f"only the approved plan is synced ({why}). Use --include-draft to preview anyway")
    actions = pm_actions(root, state)
    counts = {a: sum(1 for x in actions if x["action"] == a) for a in ("create", "update", "archive")}
    emit({"provider": state["provider_id"], "mode": state["mode"], "counts": counts,
          "actions": actions if args.json else [{k: a.get(k) for k in ("action", "id", "kind", "title", "state",
                                                                       "external_id")} for a in actions],
          "how": ("run `pm apply`" if state["provider_id"] == GH_PROVIDER else
                  "apply each action with the provider's connector tools (put the body text and the idempotency marker "
                  "in the item), then `pm record <id> --external-id … --url …` for each one")}, True)
    return 0


def _record(state: dict[str, Any], action: dict[str, Any], external_id: str, url: str | None,
            revision: str | None = None) -> None:
    item = state["items"].setdefault(action["id"], {"history": []})
    item.update({
        "kind": action.get("kind"), "external_id": str(external_id), "url": url or item.get("url"),
        "idempotency_key": action.get("idempotency_key", item.get("idempotency_key")),
        "pushed_digest": action.get("digest"), "pushed_state": action.get("state"),
        "pushed_at": iso(now()), "provider_revision": revision or item.get("provider_revision"),
        "archived": action["action"] == "archive",
    })
    item["history"].append({"at": iso(now()), "action": action["action"], "external_id": str(external_id),
                            "provider": state.get("provider_id")})


def cmd_pm_record(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    _require_provider(state)
    by_id = {a["id"]: a for a in pm_actions(root, state)}
    current = {it["id"]: it for it in desired_items(root)}
    if args.id not in current and args.id not in state["items"]:
        raise AnposError(f"{args.id} is neither in the plan nor in the sync state")
    action = by_id.get(args.id) or dict(current[args.id], action="update")
    _record(state, action, args.external_id, args.url, args.revision)
    state["last_sync_at"] = iso(now())
    write_json(root, F_PMSTATE, state)
    print(f"recorded {args.id} -> {args.external_id}")
    return 0


def _gh(*cmd: str, stdin: str | None = None) -> str:
    proc = subprocess.run(["gh", *cmd], input=stdin, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AnposError(f"gh {' '.join(cmd[:3])} … failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def cmd_pm_apply(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    _require_provider(state)
    if state["provider_id"] != GH_PROVIDER:
        raise AnposError("`pm apply` drives GitHub directly. For other providers use the connector and `pm record`")
    approved, why = plan_is_approved(root)
    if not approved:
        raise AnposError(f"only the approved plan is synced ({why})")
    ok, detail = gh_ready()
    slug = git_remote_slug(root)
    if not ok or not slug:
        raise AnposError(f"GitHub sync needs gh login and a github remote ({detail})")
    actions = pm_actions(root, state)
    if args.dry_run:
        emit([{k: a.get(k) for k in ("action", "id", "title", "state", "external_id")} for a in actions], True)
        return 0
    done = 0
    milestones: dict[str, int] | None = None

    def milestone_map() -> dict[str, int]:
        nonlocal milestones
        if milestones is None:
            raw = _gh("api", f"repos/{slug}/milestones?state=all&per_page=100")
            milestones = {m["title"]: m["number"] for m in json.loads(raw or "[]")}
        return milestones

    try:
        _gh("label", "create", "anpos", "-R", slug, "--color", "5319E7", "--description", "ANPOS work unit", "--force")
        for a in [x for x in actions if x.get("kind") == "phase"] + [x for x in actions if x.get("kind") != "phase"]:
            if a["kind"] == "phase":
                if a["action"] == "archive":
                    _gh("api", "-X", "PATCH", f"repos/{slug}/milestones/{a['external_id']}", "-f", "state=closed")
                    _record(state, a, a["external_id"], a.get("url"))
                elif a["action"] == "create" and a["title"] in milestone_map():
                    number = milestone_map()[a["title"]]
                    _gh("api", "-X", "PATCH", f"repos/{slug}/milestones/{number}", "-f", f"description={a['body']}",
                        "-f", f"state={a['state']}")
                    _record(state, a, str(number), f"https://github.com/{slug}/milestone/{number}")
                elif a["action"] == "create":
                    out = json.loads(_gh("api", "-X", "POST", f"repos/{slug}/milestones", "-f", f"title={a['title']}",
                                         "-f", f"description={a['body']}", "-f", f"state={a['state']}"))
                    milestone_map()[a["title"]] = out["number"]
                    _record(state, a, str(out["number"]), out.get("html_url"))
                else:
                    _gh("api", "-X", "PATCH", f"repos/{slug}/milestones/{a['external_id']}", "-f", f"title={a['title']}",
                        "-f", f"description={a['body']}", "-f", f"state={a['state']}")
                    _record(state, a, a["external_id"], a.get("url"))
            else:
                number = a.get("external_id")
                if a["action"] == "archive":
                    _gh("issue", "close", str(number), "-R", slug, "--reason", "not planned",
                        "--comment", "Removed from the approved ANPOS plan.")
                    _record(state, a, number, a.get("url"))
                    done += 1
                    continue
                if a["action"] == "create":
                    found = json.loads(_gh("issue", "list", "-R", slug, "--state", "all", "--search",
                                           f"\"{a['idempotency_key']}\" in:body", "--json", "number,url", "--limit", "5") or "[]")
                    if found:
                        number, url = found[0]["number"], found[0]["url"]
                        a = dict(a, action="update")
                    else:
                        cmd = ["issue", "create", "-R", slug, "--title", a["title"], "--body-file", "-", "--label", "anpos"]
                        if a.get("phase_title") and a["phase_title"] in milestone_map():
                            cmd += ["--milestone", a["phase_title"]]
                        url = _gh(*cmd, stdin=a["body"]).splitlines()[-1].strip()
                        number = url.rstrip("/").rsplit("/", 1)[-1]
                        if a["state"] == "closed":
                            _gh("issue", "close", str(number), "-R", slug, "--comment", "Already complete in the repository.")
                        _record(state, a, str(number), url)
                        if state["project"].get("url") and "/projects/" in state["project"]["url"]:
                            m = re.search(r"github\.com/(?:orgs|users)/([^/]+)/projects/(\d+)", state["project"]["url"])
                            if m:
                                _gh("project", "item-add", m.group(2), "--owner", m.group(1), "--url", url)
                        done += 1
                        continue
                else:
                    url = a.get("url")
                cmd = ["issue", "edit", str(number), "-R", slug, "--title", a["title"], "--body-file", "-"]
                if a.get("phase_title") and a["phase_title"] in milestone_map():
                    cmd += ["--milestone", a["phase_title"]]
                _gh(*cmd, stdin=a["body"])
                current = json.loads(_gh("issue", "view", str(number), "-R", slug, "--json", "state,updatedAt"))
                if a["state"] == "closed" and current["state"].upper() == "OPEN":
                    _gh("issue", "close", str(number), "-R", slug, "--comment", "Completed in the repository (ANPOS evidence recorded).")
                elif a["state"] == "open" and current["state"].upper() == "CLOSED":
                    _gh("issue", "reopen", str(number), "-R", slug)
                _record(state, a, str(number), url)
            done += 1
        state["last_error"] = None
    except AnposError as exc:
        state["last_error"] = str(exc)
        raise
    finally:
        state["last_sync_at"] = iso(now())
        write_json(root, F_PMSTATE, state)
    print(f"GitHub sync: {done} change(s) applied to {slug}")
    return 0


PROVIDER_DONE = {"closed", "done", "completed", "complete", "resolved", "canceled", "cancelled"}


def cmd_pm_pull(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    _require_provider(state)
    plan = read_json(root, F_PLAN)
    units = {w["id"]: w for w in plan.get("work_units", [])}
    observed: list[dict[str, Any]] = []
    if args.from_json:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        by_ext = {v.get("external_id"): k for k, v in state["items"].items()}
        for row in raw:
            item_id = row.get("id") or by_ext.get(str(row.get("external_id")))
            if item_id:
                observed.append({"id": item_id, "state": str(row.get("state", "")).lower(), "revision": row.get("revision")})
    elif state["provider_id"] == GH_PROVIDER:
        slug = git_remote_slug(root)
        for item_id, item in state["items"].items():
            if item.get("kind") == "work_unit" and item.get("external_id") and not item.get("archived"):
                info = json.loads(_gh("issue", "view", item["external_id"], "-R", slug, "--json", "state,updatedAt"))
                observed.append({"id": item_id, "state": info["state"].lower(), "revision": info.get("updatedAt")})
    else:
        raise AnposError("for this provider, read the item states with its connector and pass them with --from-json FILE "
                         "([{\"id\" or \"external_id\", \"state\", \"revision\"}])")

    new_conflicts, unchanged = [], 0
    for obs in observed:
        item = state["items"].get(obs["id"])
        unit = units.get(obs["id"])
        if not item or not unit:
            continue
        if obs.get("revision") and obs["revision"] == item.get("provider_revision"):
            unchanged += 1
            continue
        provider_done = obs["state"] in PROVIDER_DONE
        repo_done = unit.get("status") in DONE_WU
        echo = (provider_done and item.get("pushed_state") == "closed") or (not provider_done and item.get("pushed_state") == "open")
        if provider_done != repo_done and not echo:
            conflict = {
                "id": f"SYNC-CONFLICT-{int(state.get('next_conflict') or 1):03d}", "item_id": obs["id"],
                "field": "work_unit_completion", "repository_value": unit.get("status"), "provider_value": obs["state"],
                "repository_revision": git(root, "rev-parse", "HEAD", check=False) or None,
                "provider_revision": obs.get("revision"), "authority": "repository",
                "resolution": None, "resolved_at": None, "detected_at": iso(now()),
            }
            state["next_conflict"] = int(state.get("next_conflict") or 1) + 1
            state["conflicts"].append(conflict)
            new_conflicts.append(conflict)
        item["provider_revision"] = obs.get("revision")
        item["last_seen_state"] = obs["state"]
    state["last_pull_at"] = iso(now())
    write_json(root, F_PMSTATE, state)
    emit({"checked": len(observed), "unchanged": unchanged, "new_conflicts": new_conflicts,
          "rule": "Repository evidence decides work-unit completion. A provider-side close or reopen is recorded as a "
                  "conflict for a human, never applied silently."}, True)
    return 0


def cmd_pm_conflicts(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    if args.resolve:
        conflict = next((c for c in state["conflicts"] if c["id"] == args.resolve), None)
        if not conflict:
            raise AnposError(f"unknown conflict {args.resolve}")
        if not args.resolution:
            raise AnposError("--resolution is required (what was decided and why)")
        conflict["resolution"] = args.resolution
        conflict["resolved_at"] = iso(now())
        write_json(root, F_PMSTATE, state)
        print(f"{args.resolve} resolved")
        return 0
    emit([c for c in state["conflicts"] if args.all or not c.get("resolved_at")], True)
    return 0


def cmd_pm_status(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    state = pm_load(root)
    pending = None
    if state.get("provider_id") and plan_is_approved(root)[0]:
        pending = len(pm_actions(root, state))
    emit({"provider": state.get("provider_id"), "mode": state.get("mode"), "sync_enabled": state.get("sync_enabled"),
          "project": state.get("project"), "mapped_items": len(state.get("items", {})), "pending_changes": pending,
          "open_conflicts": sum(1 for c in state.get("conflicts", []) if not c.get("resolved_at")),
          "last_sync_at": state.get("last_sync_at"), "last_pull_at": state.get("last_pull_at"),
          "last_error": state.get("last_error")}, True)
    return 0


# ---------------------------------------------------------------- shared hooks into the governance / team modules

def audit(root: Path, action: str, target: str, result: str, consent_ref: str | None = None, **kw: Any) -> None:
    import anpos_govern
    anpos_govern.audit_log(root, action, target, result, consent_ref, **kw)


def anpos_team_propose(root: Path, title: str, summary: str, readable: list[str], action: str,
                       payload: dict[str, Any]) -> int:
    import anpos_team
    return anpos_team.propose_action(root, title, summary, readable, action, payload)


# ---------------------------------------------------------------- circuit breaker

def cmd_breaker(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    path = root / ".anpos" / "runtime" / "breaker.json"
    if args.breaker_cmd == "reset":
        path.unlink(missing_ok=True)
        print("circuit breaker reset")
        return 0
    try:
        emit(json.loads(path.read_text(encoding="utf-8")), True)
    except (OSError, json.JSONDecodeError):
        emit({}, True)
    return 0


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anpos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="project root (default: git top level of the current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="classify the repository").set_defaults(func=cmd_detect)

    s = sub.add_parser("init", help="install planner files (lite) or the full ANPOS child (--full); never overwrites")
    s.add_argument("--project-name")
    s.add_argument("--full", action="store_true", help="install the complete ANPOS control plane (bootstrap or adoption)")
    s.add_argument("--dry-run", action="store_true", help="with --full: show what would be added/merged/kept")
    s.add_argument("--repository", help="owner/name when the git remote is missing")
    s.add_argument("--github-owner", help="GitHub user/team for CODEOWNERS (default: repository owner)")
    s.add_argument("--branch", help="adoption branch name (default anpos/adopt)")
    s.add_argument("--no-branch", action="store_true", help="adopt on the current branch")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("stage", help="move the lifecycle stage (gated)")
    s.add_argument("stage")
    s.add_argument("--revise", action="store_true", help="return an approved/proposed plan to planning")
    s.add_argument("--not-applicable", metavar="REASON", help="leave data_flow / ui_ux / operations as not applicable")
    s.set_defaults(func=cmd_stage)

    s = sub.add_parser("stage-check", help="what still blocks leaving a lifecycle stage")
    s.add_argument("stage", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stage_check)

    ev = sub.add_parser("evidence", help="research evidence registry").add_subparsers(dest="evidence_cmd", required=True)
    s = ev.add_parser("add", help="append an EVID-* entry")
    s.add_argument("--claim", required=True)
    s.add_argument("--source-type", required=True, choices=["primary", "secondary", "user", "repository"])
    s.add_argument("--source-ref", required=True, help="URL, file path or 'user intake'")
    s.add_argument("--title")
    s.add_argument("--publisher")
    s.add_argument("--published-at")
    s.add_argument("--accessed-at")
    s.add_argument("--class", dest="evidence_class", required=True, choices=["verified_fact", "supported_inference", "unknown"])
    s.add_argument("--confidence", required=True, choices=["low", "medium", "high"])
    s.add_argument("--summary", required=True)
    s.add_argument("--req", action="append")
    s.add_argument("--opt", action="append")
    s.add_argument("--mod", action="append")
    s.add_argument("--supersedes", action="append")
    s.add_argument("--validation-status", default="unverified",
                   choices=["unverified", "validated", "rejected", "superseded", "conflicting"])
    s.add_argument("--note", action="append")
    s.set_defaults(func=cmd_evidence_add)

    cs = sub.add_parser("consent", help="approval gates").add_subparsers(dest="consent_cmd", required=True)
    s = cs.add_parser("propose", help="create a pending consent request with an approval code")
    s.add_argument("--type", required=True, choices=sorted(CONSENT_KIND))
    s.add_argument("--title")
    s.add_argument("--summary")
    s.add_argument("--change", action="append", help="one proposed change / decision (repeatable)")
    s.add_argument("--module", action="append")
    s.add_argument("--risk", default="unknown", choices=["low", "medium", "high", "unknown"])
    s.add_argument("--expires-hours", type=int, default=72)
    s.set_defaults(func=cmd_consent_propose)
    s = cs.add_parser("confirm", help="record the decision the user typed")
    s.add_argument("--id")
    s.set_defaults(func=cmd_consent_confirm)

    s = sub.add_parser("validate", help="schema + cross-reference + gate checks")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("status", help="human readable project status")
    s.add_argument("--brief", action="store_true", help="one line, silent outside ANPOS projects")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("next", help="ready work units of the approved plan")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_next)

    sub.add_parser("sync-state", help="recompute project-state progress").set_defaults(func=cmd_sync_state)

    pm = sub.add_parser("pm", help="project management selection and sync").add_subparsers(dest="pm_cmd", required=True)
    pm.add_parser("providers", help="list providers and how each can connect").set_defaults(func=cmd_pm_providers)
    s = pm.add_parser("select", help="select and map a provider")
    s.add_argument("provider")
    s.add_argument("--mode", choices=["host_connector", "oauth", "mcp", "authenticated_api", "git_native"])
    s.add_argument("--workspace-id")
    s.add_argument("--workspace-name")
    s.add_argument("--project-id")
    s.add_argument("--project-name")
    s.add_argument("--project-url", help="external project URL (GitHub: optional Projects board URL)")
    s.add_argument("--verified", action="store_true", help="the mapping was confirmed with the provider")
    s.add_argument("--switch", action="store_true", help="switch away from a provider that already has mapped items")
    s.set_defaults(func=cmd_pm_select)
    pm.add_parser("skip", help="continue without a PM tool").set_defaults(func=cmd_pm_skip)
    s = pm.add_parser("plan", help="what a sync would create/update/archive")
    s.add_argument("--json", action="store_true", help="include full bodies for connector use")
    s.add_argument("--include-draft", action="store_true")
    s.set_defaults(func=cmd_pm_plan)
    s = pm.add_parser("apply", help="GitHub: create/update milestones and issues with gh")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_pm_apply)
    s = pm.add_parser("record", help="record an item created/updated through a connector")
    s.add_argument("id")
    s.add_argument("--external-id", required=True)
    s.add_argument("--url")
    s.add_argument("--revision")
    s.set_defaults(func=cmd_pm_record)
    s = pm.add_parser("pull", help="read provider states and record conflicts (repository stays canonical)")
    s.add_argument("--from-json", help="states read through a connector")
    s.set_defaults(func=cmd_pm_pull)
    s = pm.add_parser("conflicts", help="list or resolve sync conflicts")
    s.add_argument("--all", action="store_true")
    s.add_argument("--resolve")
    s.add_argument("--resolution")
    s.set_defaults(func=cmd_pm_conflicts)
    pm.add_parser("status", help="sync status").set_defaults(func=cmd_pm_status)

    import anpos_team
    anpos_team.register(sub)
    import anpos_govern
    anpos_govern.register(sub)

    br = sub.add_parser("breaker", help="circuit breaker for repeated identical failures").add_subparsers(dest="breaker_cmd", required=True)
    br.add_parser("status").set_defaults(func=cmd_breaker)
    br.add_parser("reset").set_defaults(func=cmd_breaker)

    s = sub.add_parser("export", help="export work units for PM tools / GitHub issues")
    s.add_argument("--format", choices=["md", "csv", "issues"], default="md",
                   help="md = readable plan, csv = spreadsheet/PM import, issues = JSON list of {title, body, labels}")
    s.add_argument("--output", help="file path (relative to project root); default: stdout")
    s.add_argument("--only-open", action="store_true", help="skip complete/deprecated work units")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("wu", help="update a work unit of the approved plan")
    s.add_argument("id")
    s.add_argument("status", choices=["ready", "in_progress", "blocked", "verification_required", "complete",
                                      "deferred", "needs_update", "needs_removal"])
    s.add_argument("--evidence", action="append")
    s.add_argument("--blocker", action="append")
    s.set_defaults(func=cmd_wu)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AnposError as exc:
        print(f"anpos: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
