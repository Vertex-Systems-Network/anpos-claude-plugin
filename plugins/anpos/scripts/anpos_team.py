"""Development AI selection, Supervisor/Worker coordination and runtime budgets for the anpos plugin.

Implements a *local runtime adapter* for the upstream ANPOS multi-agent protocol (AUTO-AGENT.md,
SUPERVISOR.md, CONTROL-PLANE-SECURITY.md):

* the agent pool lives in config/ai/agent-catalog.json and is registered only through a human
  `APPROVE CHANGE` consent; identities are host-attested by this plugin (local evidence, not
  cryptographic identity);
* the Supervisor lease and Worker claims are atomic, epoch-fenced local git refs under
  refs/anpos/ (the upstream reference scripts use GitHub refs; this is the "equivalent adapter"
  they allow for single-machine runtimes);
* authorization reuses the project's own scripts/anpos_guard.py; shared coordination writes go
  through the project's own scripts/coordination_mutation.py (fenced CAS gateway);
* Workers run in git worktrees; submission is verified with `git diff` against allowed paths.

Requires a full ANPOS child (`anpos init --full`).
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import anpos as core
from anpos import AnposError, emit, find_root, iso, now, parse_time, read_json, write_json

F_CATALOG = "config/ai/agent-catalog.json"
F_SUP = "config/coordination/supervisor-state.json"
F_QUEUE = "config/coordination/agent-work-queue.json"
F_BUDGET = "config/runtime/budgets.json"
F_ALERTS = "config/coordination/agent-alerts.json"
F_MERGE = "config/coordination/merge-events.json"
F_POLICY = "config/security/control-plane-policy.json"
RUNTIME = ".anpos/runtime"
IDENTITY_DIR = ".anpos/identity"
WORKTREES = ".anpos/worktrees"
ACTIVE = {"claimed", "in_progress", "blocked", "submitted_for_review", "changes_requested", "approved"}

KNOWN_CLIS = {
    "codex": {"name": "Codex CLI", "provider": "openai", "handoff": "codex exec (check `codex --help`)"},
    "gemini": {"name": "Gemini CLI", "provider": "google", "handoff": "gemini -p (check `gemini --help`)"},
    "copilot": {"name": "GitHub Copilot CLI", "provider": "github", "handoff": "copilot -p (check `copilot --help`)"},
    "cursor-agent": {"name": "Cursor Agent CLI", "provider": "cursor", "handoff": "cursor-agent -p (check `cursor-agent --help`)"},
}
CLAUDE_AGENTS = {
    "claude-supervisor": {
        "name": "Claude Code (main session)", "provider": "anthropic", "roles": ["supervisor"],
        "capabilities": ["coordination_admin", "repository_write", "control_plane_write"],
        "handoff": "this Claude Code session",
    },
    "claude-worker": {
        "name": "Claude Code worker subagent (anpos:worker)", "provider": "anthropic", "roles": ["worker"],
        "capabilities": ["repository_write"],
        "handoff": "Claude Code subagent `anpos:worker` in a git worktree",
    },
}


# ---------------------------------------------------------------- helpers

def require_full(root: Path) -> None:
    if not core.is_full_child(root):
        raise AnposError("multi-agent coordination needs a full ANPOS child (run `init --full` first)")


def git(root: Path, *args: str, check: bool = True) -> str:
    return core.git(root, *args, check=check)


def repo_key(root: Path) -> str:
    try:
        inst = json.loads((root / "config/protocol/instance.json").read_text()).get("instance_id")
    except (OSError, json.JSONDecodeError):
        inst = None
    return inst or core.git_remote_slug(root) or root.name


def principal_for(root: Path, agent_id: str) -> str:
    return f"anpos-local:{agent_id}@{repo_key(root)}"


def runtime_path(root: Path, *parts: str) -> Path:
    path = root / RUNTIME
    path.mkdir(parents=True, exist_ok=True)
    gi = root / ".anpos" / ".gitignore"
    if not gi.exists() or "runtime/" not in gi.read_text():
        gi.write_text("runtime/\nworktrees/\n", encoding="utf-8")
    target = path.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def load_upstream_guard(root: Path, principal: str | None):
    if principal:
        os.environ["ANPOS_RUNTIME_PRINCIPAL"] = principal
    spec = importlib.util.spec_from_file_location("anpos_guard_project", root / "scripts" / "anpos_guard.py")
    if not spec or not spec.loader:
        raise AnposError("scripts/anpos_guard.py is missing from the project")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guarded(fn, *args):
    try:
        return fn(*args)
    except (PermissionError, ValueError) as exc:
        raise AnposError(f"authorization failed: {exc}")


def create_ref(root: Path, ref: str, sha: str) -> None:
    """Atomic create-only ref (fails when it already exists): the local lock primitive."""
    proc = subprocess.run(["git", "update-ref", ref, sha, ""], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AnposError(f"lock lost: {ref} already exists or could not be created ({proc.stderr.strip()})")


def delete_ref(root: Path, ref: str) -> None:
    subprocess.run(["git", "update-ref", "-d", ref], cwd=root, capture_output=True, text=True)


def gateway_write(root: Path, target: str, new_doc: dict[str, Any]) -> None:
    """Shared coordination write through the project's own fenced CAS gateway."""
    sup = supervisor_runtime(root)
    old_text = (root / target).read_text(encoding="utf-8")
    tmp = runtime_path(root, f"gateway-{uuid.uuid4().hex}.json")
    tmp.write_text(json.dumps(new_doc, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ, ANPOS_RUNTIME_PRINCIPAL=principal_for(root, sup["agent_id"]))
    cmd = [sys.executable, "scripts/coordination_mutation.py", "--target", target, "--input", str(tmp),
           "--expected-sha256", hashlib.sha256(old_text.encode("utf-8")).hexdigest(),
           "--supervisor-agent-id", sup["agent_id"], "--coordination-epoch", str(sup["epoch"]),
           "--fencing-token", sup["fencing_token"], "--apply"]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=env)
    tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AnposError(f"coordination gateway refused the write to {target}: "
                         + (proc.stderr.strip() or proc.stdout.strip())[-600:])


COORDINATION_STATE = ("config/coordination/", "config/ai/execution-plan.json", "config/ai/project-state.json",
                      "config/ai/agent-catalog.json", "config/runtime/", "config/consent/", "config/audit/",
                      "config/integrations/", ".anpos/")


def dirty_outside_coordination(root: Path) -> list[str]:
    """Uncommitted changes other than ANPOS coordination state (which changes constantly during a team run)."""
    out = []
    raw = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    for line in raw.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if not path.startswith(COORDINATION_STATE):
            out.append(path)
    return out


def supervisor_runtime(root: Path) -> dict[str, Any]:
    path = root / RUNTIME / "supervisor.json"
    if not path.exists():
        raise AnposError("no Supervisor lease held in this runtime (run `team supervisor acquire`)")
    sup = json.loads(path.read_text())
    state = read_json(root, F_SUP)
    live = state.get("supervisor") or {}
    expires = parse_time(live.get("lease_expires_at")) if live.get("lease_expires_at") else None
    if (live.get("status") != "active" or live.get("fencing_token") != sup["fencing_token"]
            or not expires or expires <= now()):
        raise AnposError("the Supervisor lease is no longer live (expired, released or taken over). "
                         "Re-acquire with `team supervisor acquire`")
    return sup


def _encode(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()


def _decode(text: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(text.encode()).decode())


def propose_action(root: Path, title: str, summary: str, readable: list[str], action: str,
                   payload: dict[str, Any], risk: str = "medium") -> int:
    """Create a scope_change consent whose hashed content carries the exact action payload."""
    ns = argparse.Namespace(root=str(root), type="scope_change", title=title, summary=summary,
                            change=readable + [f"action:{action}:{_encode(payload)}"], module=[], risk=risk,
                            expires_hours=72)
    return core.cmd_consent_propose(ns)


def apply_consented_actions(root: Path, record: dict[str, Any]) -> list[str]:
    """Called by `consent confirm` after an approval: run the approved action payloads."""
    done = []
    for change in record.get("proposed_changes", []):
        if not str(change).startswith("action:"):
            continue
        _, name, blob = str(change).split(":", 2)
        import anpos_govern
        handler = ACTIONS.get(name) or anpos_govern.ACTIONS.get(name)
        if not handler:
            raise AnposError(f"approved consent carries an unknown action {name}")
        handler(root, _decode(blob), record)
        done.append(name)
    return done


# ---------------------------------------------------------------- Development AI selection

def detect_agents(root: Path) -> list[dict[str, Any]]:
    found = []
    claude_bin = shutil.which("claude")
    version = None
    if claude_bin:
        proc = subprocess.run([claude_bin, "--version"], capture_output=True, text=True)
        version = proc.stdout.strip() or None
    for aid, spec in CLAUDE_AGENTS.items():
        found.append({"id": aid, "name": spec["name"], "provider": spec["provider"], "roles": spec["roles"],
                      "capabilities": spec["capabilities"], "available": True, "invokable": True,
                      "attachable": True, "connection_or_handoff_method": spec["handoff"],
                      "detection": {"host": "claude-code", "binary": claude_bin, "version": version}})
    for binary, spec in KNOWN_CLIS.items():
        path = shutil.which(binary)
        if not path:
            continue
        try:
            proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
            ver = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else None
        except (OSError, subprocess.TimeoutExpired):
            ver = None
        found.append({"id": f"{binary}-worker", "name": spec["name"], "provider": spec["provider"], "roles": ["worker"],
                      "capabilities": ["repository_write"], "available": True, "invokable": True, "attachable": True,
                      "connection_or_handoff_method": spec["handoff"],
                      "detection": {"binary": path, "version": ver}})
    return found


def cmd_agents_detect(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    agents = detect_agents(root)
    catalog = read_json(root, F_CATALOG)
    catalog["available_agents"] = [{k: v for k, v in a.items() if k != "detection"} | {"detection_at": iso(now())}
                                   for a in agents]
    unavailable = [spec["name"] for b, spec in KNOWN_CLIS.items() if not any(a["id"] == f"{b}-worker" for a in agents)]
    catalog["suggested_but_unavailable"] = unavailable
    if catalog.get("selection_status") in (None, "discovery_required"):
        catalog["selection_status"] = "discovered"
    write_json(root, F_CATALOG, catalog)
    emit({"available": [{k: a[k] for k in ("id", "name", "roles", "connection_or_handoff_method")} for a in agents],
          "not_installed": unavailable,
          "next": "`agents select --supervisor claude-supervisor --worker claude-worker [--worker codex-worker]` "
                  "creates the APPROVE CHANGE request"}, True)
    return 0


def _profile(spec: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    permissions = {
        "allowed_paths": args.allowed_path or ["**"],
        "denied_paths": args.denied_path or [],
        "allowed_tools": [],
        "network_policy": "deny_unless_required",
        "network_allowlist": [],
        "secret_scope": "none_by_default",
        "pm_scope": "minimum_required",
        "deployment_scope": "none_by_default",
        "repository_admin": "supervisor" in spec["roles"],
        "destructive_actions": False,
    }
    return permissions


def cmd_agents_select(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    available = {a["id"]: a for a in detect_agents(root)}
    wanted = [(args.supervisor, "supervisor")] + [(w, "worker") for w in (args.worker or [])]
    if not args.supervisor or not args.worker:
        raise AnposError("choose one --supervisor and at least one --worker")
    agents = []
    for aid, role in wanted:
        if aid not in available:
            raise AnposError(f"{aid} is not available in this host (run `agents detect`)")
        spec = available[aid]
        if role not in spec["roles"]:
            raise AnposError(f"{aid} cannot take the {role} role")
        agents.append({"id": aid, "role": role, "name": spec["name"], "provider": spec["provider"],
                       "capabilities": spec["capabilities"], "permissions": _profile(spec, args),
                       "handoff": spec["connection_or_handoff_method"], "detection": spec.get("detection")})
    readable = [f"{a['id']} ({a['name']}) as {a['role']}: capabilities {', '.join(a['capabilities'])}; "
                f"paths {', '.join(a['permissions']['allowed_paths'])}; no secrets, no deployment, network denied unless required"
                for a in agents]
    return propose_action(root, "Register Development AI pool",
                          "Selects the agents ANPOS may run as Supervisor and Workers in this repository.",
                          readable, "agents_select", {"agents": agents, "ttl_days": args.ttl_days})


def _apply_agents_select(root: Path, payload: dict[str, Any], record: dict[str, Any]) -> None:
    catalog = read_json(root, F_CATALOG)
    verified = now()
    expires = verified + timedelta(days=int(payload.get("ttl_days") or 30))
    (root / IDENTITY_DIR).mkdir(parents=True, exist_ok=True)
    selected, workers, supervisor = [], [], None
    for a in payload["agents"]:
        evidence_rel = f"{IDENTITY_DIR}/{a['id']}.json"
        evidence = {
            "agent_id": a["id"], "principal_id": principal_for(root, a["id"]),
            "principal_type": "host_attested_local_agent", "attested_by": "anpos Claude Code plugin",
            "approved_by_consent": record["id"], "decision_evidence": record.get("decision_identity_evidence_ref"),
            "detection": a.get("detection"), "verified_at": iso(verified), "expires_at": iso(expires),
            "limits": "Local host attestation: proves the human approved this agent for this repository and the host "
                      "detected it; it is not a cryptographic identity of the model or vendor.",
        }
        write_json(root, evidence_rel, evidence)
        privacy = {
            "repository_data_scope": "local repository through the agent's own client",
            "external_network_enabled": None, "data_retention": "unknown", "training_use": "unknown",
            "geography_or_enterprise_constraints": [], "sensitive_data_allowed": False,
            "notes": f"Check {a['provider']}'s plan/data settings; unknowns stay explicit.",
        }
        selected.append({
            "id": a["id"], "name": a["name"], "provider": a["provider"], "capabilities": a["capabilities"],
            "available": True, "attachable": True, "invokable": True, "identity_verified": True,
            "runtime_identity": {"principal_type": "host_attested_local_agent",
                                 "principal_id": principal_for(root, a["id"]), "provider": a["provider"],
                                 "evidence_ref": evidence_rel, "verified_at": iso(verified), "expires_at": iso(expires)},
            "permissions": a["permissions"], "privacy_profile": privacy, "roles": [a["role"]],
            "selection_action": f"Use {a['name']}", "connection_or_handoff_method": a["handoff"],
            "notes": f"Registered through {record['id']}",
        })
        if a["role"] == "supervisor":
            supervisor = a["id"]
        else:
            workers.append(a["id"])
    catalog["selected_agents"] = selected
    catalog["role_assignments"] = {"supervisor_agent_id": supervisor, "worker_agent_ids": workers}
    catalog["selection_status"] = "selected_identity_verified"
    write_json(root, F_CATALOG, catalog)
    core.audit(root, "agent_pool_registered", ",".join(a["id"] for a in payload["agents"]), "registered", record["id"],
               risk="elevated")


def cmd_agents_list(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    catalog = read_json(root, F_CATALOG)
    emit({"selection_status": catalog.get("selection_status"), "role_assignments": catalog.get("role_assignments"),
          "selected": [{"id": a.get("id"), "roles": a.get("roles"), "verified": a.get("identity_verified"),
                        "expires_at": (a.get("runtime_identity") or {}).get("expires_at")}
                       for a in catalog.get("selected_agents", [])],
          "available": [a.get("id") for a in catalog.get("available_agents", [])]}, True)
    return 0


def selected_agent(root: Path, agent_id: str, role: str) -> dict[str, Any]:
    guard = load_upstream_guard(root, principal_for(root, agent_id))
    return guarded(guard.require_verified_agent, agent_id, role)


# ---------------------------------------------------------------- budgets

BUDGET_FIELDS = {"token_budget": int, "model_cost_budget": float, "ci_minutes_budget": int, "cloud_cost_budget": float,
                 "max_parallel_agents": int, "max_delegation_depth": int, "max_same_error_retries": int,
                 "max_tool_retry_attempts": int}


def cmd_budgets_show(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    b = read_json(root, F_BUDGET)
    queue = read_json(root, F_QUEUE)
    active = sum(1 for s in queue.get("slots", []) if s.get("status") in ACTIVE)
    emit({"limits": b.get("limits"), "active_slots": active, "policy": b.get("policy"),
          "unset_required": [k for k in ("token_budget", "model_cost_budget") if (b.get("limits") or {}).get(k) is None]}, True)
    return 0


def cmd_budgets_propose(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    changes = {k: getattr(args, k) for k in BUDGET_FIELDS if getattr(args, k) is not None}
    if not changes:
        raise AnposError("give at least one limit, e.g. --token-budget 2000000 --model-cost-budget 25")
    readable = [f"limits.{k} = {v}" for k, v in changes.items()]
    return propose_action(root, "Set runtime budgets", "Autonomous execution limits for agents (config/runtime/budgets.json).",
                          readable, "budgets_set", {"limits": changes})


def _apply_budgets(root: Path, payload: dict[str, Any], record: dict[str, Any]) -> None:
    b = read_json(root, F_BUDGET)
    b.setdefault("limits", {}).update(payload["limits"])
    b["last_change_consent"] = record["id"]
    write_json(root, F_BUDGET, b)
    core.audit(root, "runtime_budgets_set", F_BUDGET, json.dumps(payload["limits"], sort_keys=True), record["id"])


# ---------------------------------------------------------------- Supervisor lease

def cmd_supervisor(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = read_json(root, F_SUP)
    current = state.get("supervisor") or {}
    rt = root / RUNTIME / "supervisor.json"
    if args.action == "acquire":
        agent = selected_agent(root, args.agent_id, "supervisor")
        if (agent.get("permissions") or {}).get("repository_admin") is not True and \
                "coordination_admin" not in set(agent.get("capabilities") or []):
            raise AnposError("the selected Supervisor lacks repository_admin / coordination_admin")
        expiry = parse_time(current["lease_expires_at"]) if current.get("lease_expires_at") else None
        if current.get("status") == "active" and expiry and expiry > now():
            raise AnposError(f"an active Supervisor lease is held by {current.get('agent_id')} until {current['lease_expires_at']}")
        head = git(root, "rev-parse", "HEAD", check=False)
        if not head:
            raise AnposError("commit at least once before starting coordination")
        epoch = int(state.get("coordination_epoch") or 0) + 1
        lease_id = str(uuid.uuid4())
        ref = f"refs/anpos/supervisor/epoch-{epoch:06d}"
        create_ref(root, ref, head)
        acquired = now()
        expires = acquired + timedelta(minutes=max(5, args.lease_minutes))
        fencing = f"supervisor:{epoch}:{lease_id}"
        state["coordination_epoch"] = epoch
        state["status"] = "active"
        state["supervisor"] = {
            "status": "active", "agent_id": args.agent_id, "agent_type": "supervisor",
            "identity_ref": (agent.get("runtime_identity") or {}).get("evidence_ref"),
            "branch": ref.removeprefix("refs/"), "active_module_id": None, "active_work_unit_id": None,
            "started_at": iso(acquired), "heartbeat_at": iso(acquired), "lease_id": lease_id,
            "lease_status": "active", "lease_expires_at": iso(expires), "fencing_token": fencing,
            "election_ref": ref.removeprefix("refs/"),
        }
        state["last_reconciled_main_sha"] = head
        write_json(root, F_SUP, state)
        base_branch = git(root, "branch", "--show-current", check=False) or "HEAD"
        rt = runtime_path(root, "supervisor.json")
        rt.write_text(json.dumps({"agent_id": args.agent_id, "epoch": epoch, "fencing_token": fencing,
                                  "lease_id": lease_id, "base_branch": base_branch}, indent=2))
        core.audit(root, "supervisor_lease_acquired", f"epoch {epoch}", "active", actor=args.agent_id, role="supervisor")
        print(f"Supervisor lease acquired by {args.agent_id}: epoch {epoch}, expires {iso(expires)} (base branch {base_branch})")
        return 0
    sup = supervisor_runtime(root)
    if args.action == "heartbeat":
        expires = now() + timedelta(minutes=max(5, args.lease_minutes))
        state["supervisor"]["heartbeat_at"] = iso(now())
        state["supervisor"]["lease_expires_at"] = iso(expires)
        write_json(root, F_SUP, state)
        print(f"Supervisor lease renewed until {iso(expires)}")
    elif args.action == "release":
        queue = read_json(root, F_QUEUE)
        active = [s["id"] for s in queue.get("slots", []) if s.get("status") in ACTIVE]
        if active and not args.force:
            raise AnposError(f"workers still hold slots ({', '.join(active)}); merge/release them first or use --force")
        state["supervisor"].update({"status": "relinquished", "lease_status": "released"})
        state["status"] = "relinquished"
        write_json(root, F_SUP, state)
        delete_ref(root, f"refs/{state['supervisor'].get('election_ref')}")
        rt.unlink(missing_ok=True)
        core.audit(root, "supervisor_lease_released", f"epoch {state.get('coordination_epoch')}", "released",
                   actor=sup["agent_id"], role="supervisor")
        print("Supervisor lease released")
    return 0


# ---------------------------------------------------------------- slots and dispatch

def require_development(root: Path) -> None:
    stage = read_json(root, core.F_STATE).get("lifecycle_stage")
    if stage != "development":
        raise AnposError(f"workers build in Stage 13 (development); current stage is {stage}. "
                         "Finish the earlier lifecycle stages first (/anpos:lifecycle)")


def cmd_slots(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    supervisor_runtime(root)
    require_development(root)
    ok, why = core.plan_is_approved(root)
    if not ok:
        raise AnposError(f"slots come from the approved plan only ({why})")
    plan = read_json(root, core.F_PLAN)
    marked = []
    for unit in plan.get("work_units", []):
        if unit.get("status") == "not_started":
            unit["status"] = "ready"
            marked.append(unit["id"])
    if marked:
        write_json(root, core.F_PLAN, plan)
    out = runtime_path(root, "candidate-queue.json")
    proc = subprocess.run([sys.executable, "scripts/plan_to_slots.py", "--output", str(out)], cwd=root,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AnposError("plan_to_slots failed: " + proc.stderr.strip())
    info = json.loads(proc.stderr[proc.stderr.index("{"):])
    if info.get("added_slots"):
        gateway_write(root, F_QUEUE, json.loads(out.read_text()))
    out.unlink(missing_ok=True)
    emit({"work_units_marked_ready": marked, "added_slots": info.get("added_slots", [])}, True)
    return 0


def _deps_done(root: Path, slot: dict[str, Any], slots: list[dict[str, Any]]) -> bool:
    plan = read_json(root, core.F_PLAN)
    done = {w["id"] for w in plan.get("work_units", []) if w.get("status") in core.DONE_WU}
    done |= {s.get("work_unit_id") for s in slots if s.get("status") in {"merged", "completed"}}
    return set(slot.get("dependencies") or []).issubset(done)


def _enforce_budgets(root: Path, queue: dict[str, Any], slot: dict[str, Any]) -> None:
    limits = (read_json(root, F_BUDGET).get("limits") or {})
    unset = [k for k in ("token_budget", "model_cost_budget") if limits.get(k) is None]
    if unset:
        raise AnposError("autonomous Worker claims need explicit cost limits (null means unconfigured, not unlimited): "
                         + ", ".join(unset) + ". Propose them with `team budgets propose`.")
    active = sum(1 for s in queue.get("slots", []) if s.get("status") in ACTIVE)
    if active >= int(limits.get("max_parallel_agents") or 1):
        raise AnposError(f"parallel-agent budget reached: {active}/{limits.get('max_parallel_agents')} active slots")
    if int(slot.get("delegation_depth") or 0) > int(limits.get("max_delegation_depth") or 0):
        raise AnposError("delegation depth exceeds the configured maximum")


def _claim_file(root: Path, slot_id: str) -> Path:
    return runtime_path(root, "claims", f"{slot_id}.json")


def cmd_dispatch(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    sup = supervisor_runtime(root)
    require_development(root)
    queue = read_json(root, F_QUEUE)
    slots = [s for s in queue.get("slots", []) if isinstance(s, dict)]
    candidates = [s for s in slots if s.get("status") == "free" and _deps_done(root, s, slots)
                  and (args.slot is None or s.get("id") == args.slot)]
    if not candidates:
        raise AnposError("no dependency-satisfied free slot" + (f" named {args.slot}" if args.slot else ""))
    candidates.sort(key=lambda s: (-int(s.get("priority") or 0), str(s.get("id"))))
    slot = candidates[0]
    _enforce_budgets(root, queue, slot)
    dirty = dirty_outside_coordination(root)
    if dirty:
        raise AnposError("commit the main working tree first (workers branch from a committed base): " + ", ".join(dirty[:5]))
    base_sha = git(root, "rev-parse", "HEAD")
    slot["base_sha"] = base_sha
    guard = load_upstream_guard(root, principal_for(root, args.agent_id))
    guarded(guard.validate_handoff, slot)
    agent = guarded(guard.authorize_slot_claim, slot, args.agent_id, "worker")
    guarded(guard.authorize_slot_paths, slot, agent)
    guarded(guard.authorize_runtime_scope, slot, agent)

    epoch = sup["epoch"]
    claim_ref = f"refs/anpos/claims/epoch-{epoch:06d}/{slot['id']}"
    create_ref(root, claim_ref, base_sha)
    branch = f"anpos/{slot['work_unit_id']}"
    worktree = root / WORKTREES / slot["id"]
    try:
        runtime_path(root)  # ensures .anpos/.gitignore
        if git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
            # a released/recovered slot keeps its branch: the next worker resumes from it
            proc = subprocess.run(["git", "worktree", "add", "-q", str(worktree), branch], cwd=root,
                                  capture_output=True, text=True)
            base_sha = git(root, "merge-base", branch, "HEAD") if proc.returncode == 0 else base_sha
        else:
            proc = subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(worktree), base_sha], cwd=root,
                                  capture_output=True, text=True)
        if proc.returncode != 0:
            raise AnposError(f"could not create the worker worktree: {proc.stderr.strip()}")
        lease_id = str(uuid.uuid4())
        claimed = now()
        expires = claimed + timedelta(minutes=max(5, args.lease_minutes))
        fencing = f"worker:{epoch}:{lease_id}"
        identity_ref = (agent.get("runtime_identity") or {}).get("evidence_ref")
        for s in queue["slots"]:
            if s.get("id") == slot["id"]:
                s.update({"status": "claimed", "claim_ref": claim_ref.removeprefix("refs/"), "claim_branch": branch,
                          "claimant": args.agent_id, "claimant_identity_ref": identity_ref, "agent_type": args.agent_id,
                          "base_sha": base_sha, "claim_id": lease_id, "claim_nonce": lease_id, "lease_status": "active",
                          "lease_expires_at": iso(expires), "coordination_epoch": epoch, "claimed_at": iso(claimed),
                          "heartbeat_at": iso(claimed), "fencing_token": fencing})
        queue["updated_at"] = iso(claimed)
        write_json(root, F_QUEUE, queue)  # the claim mirror itself, as upstream claim_slot.py does
        q2 = read_json(root, F_QUEUE)
        for s in q2["slots"]:
            if s.get("id") == slot["id"]:
                s["status"] = "in_progress"
        gateway_write(root, F_QUEUE, q2)
    except Exception:
        delete_ref(root, claim_ref)
        raise
    caps = set(agent.get("capabilities") or [])
    claim = {"slot_id": slot["id"], "work_unit_id": slot["work_unit_id"], "agent_id": args.agent_id,
             "worktree": str(worktree), "branch": branch, "base_sha": base_sha, "base_branch": sup.get("base_branch"),
             "fencing_token": fencing,
             "allowed_paths": slot.get("allowed_paths") or [], "denied_paths": slot.get("denied_paths") or [],
             "control_plane_write": "control_plane_write" in caps, "claimed_at": iso(claimed)}
    _claim_file(root, slot["id"]).write_text(json.dumps(claim, indent=2))
    core.audit(root, "worker_slot_claimed", slot["id"], "in_progress", actor=args.agent_id, role="worker",
               result_ref=claim_ref.removeprefix("refs/"))
    brief = {
        **claim, "title": slot.get("title"), "objective": slot.get("description"),
        "acceptance_criteria": slot.get("acceptance_criteria"), "required_checks": slot.get("required_checks"),
        "requirement_ids": slot.get("requirement_ids"), "lease_expires_at": iso(expires),
        "worker_rules": [
            f"Work only inside {worktree} on branch {branch}; change only files matching allowed_paths.",
            "Run every required check; commit your work on the branch (no pushes, no merges).",
            f"Long task: `team heartbeat --slot {slot['id']} --agent-id {args.agent_id}` before the lease expires.",
            f"If you need work merged meanwhile, merge the base branch into yours (`git -C {worktree} merge "
            f"{sup.get('base_branch')}`) and rerun the checks; only your own changes are reviewed.",
            f"Finish with `team submit --slot {slot['id']} --agent-id {args.agent_id} --evidence \"<checks + results>\"`.",
            "Then reply exactly: ALL DONE SUBMITTED FOR REVIEW AND MERGE",
        ],
    }
    emit(brief, True)
    return 0


def _slot(queue: dict[str, Any], slot_id: str) -> dict[str, Any]:
    for s in queue.get("slots", []):
        if s.get("id") == slot_id:
            return s
    raise AnposError(f"unknown slot {slot_id}")


def _claim(root: Path, slot_id: str, agent_id: str | None = None) -> dict[str, Any]:
    path = root / RUNTIME / "claims" / f"{slot_id}.json"
    if not path.exists():
        raise AnposError(f"{slot_id} has no active claim in this runtime")
    claim = json.loads(path.read_text())
    if agent_id and claim["agent_id"] != agent_id:
        raise AnposError(f"{slot_id} is claimed by {claim['agent_id']}, not {agent_id}")
    return claim


def cmd_heartbeat(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    claim = _claim(root, args.slot, args.agent_id)
    queue = read_json(root, F_QUEUE)
    slot = _slot(queue, args.slot)
    if slot.get("fencing_token") != claim["fencing_token"] or slot.get("lease_status") != "active":
        raise AnposError("the worker lease is no longer valid (revoked or recovered); stop and report")
    if parse_time(slot["lease_expires_at"]) <= now():
        raise AnposError("the worker lease expired; this worker is read-only for the slot now")
    expires = now() + timedelta(minutes=max(5, args.lease_minutes))
    slot.update({"heartbeat_at": iso(now()), "lease_expires_at": iso(expires)})
    write_json(root, F_QUEUE, queue)  # heartbeat mirror, as upstream lease_control.py does
    print(f"{args.slot} lease renewed until {iso(expires)}")
    return 0


def _protected(root: Path) -> list[str]:
    try:
        return read_json(root, F_POLICY).get("protected_paths") or []
    except AnposError:
        return []


def path_violations(root: Path, claim: dict[str, Any], files: list[str]) -> list[str]:
    bad = []
    protected = [p.lstrip("/") for p in _protected(root)]
    for f in files:
        if not any(fnmatch.fnmatch(f, p.lstrip("/")) for p in claim["allowed_paths"]):
            bad.append(f"{f} (outside allowed_paths)")
        elif any(fnmatch.fnmatch(f, p.lstrip("/")) for p in claim["denied_paths"]):
            bad.append(f"{f} (denied path)")
        elif not claim.get("control_plane_write") and any(fnmatch.fnmatch(f, p) or f == p for p in protected):
            bad.append(f"{f} (protected ANPOS control plane)")
    return bad


def own_changes(root: Path, claim: dict[str, Any]) -> tuple[list[str], int]:
    """Files the worker changed since its branch last met the base branch (merging the base in is fine)."""
    base = claim.get("base_branch") or claim["base_sha"]
    if not git(root, "rev-parse", "--verify", "--quiet", base, check=False):
        base = claim["base_sha"]
    files = [f for f in git(root, "diff", "--name-only", f"{base}...{claim['branch']}").splitlines() if f]
    ahead = int(git(root, "rev-list", "--count", "--no-merges", f"{base}..{claim['branch']}") or 0)
    return files, ahead


def cmd_submit(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    claim = _claim(root, args.slot, args.agent_id)
    queue = read_json(root, F_QUEUE)
    slot = _slot(queue, args.slot)
    if slot.get("fencing_token") != claim["fencing_token"] or slot.get("lease_status") != "active":
        raise AnposError("the worker lease is no longer valid; submission refused")
    if parse_time(slot["lease_expires_at"]) <= now():
        raise AnposError("the worker lease expired; submission refused (ask the Supervisor to recover/re-dispatch)")
    wt = Path(claim["worktree"])
    if git(wt, "status", "--porcelain"):
        raise AnposError("commit everything in the worktree first (uncommitted changes)")
    head = git(wt, "rev-parse", "HEAD")
    files, ahead = own_changes(root, claim)
    if ahead < 1:
        raise AnposError("no commits on the work branch yet")
    bad = path_violations(root, claim, files)
    if bad:
        raise AnposError("changes outside the slot's permissions:\n  - " + "\n  - ".join(bad))
    if not args.evidence:
        raise AnposError("--evidence is required (which checks ran and their results)")
    sub = {"slot_id": args.slot, "agent_id": args.agent_id, "head_sha": head, "commits": ahead, "files": files,
           "evidence": args.evidence, "submitted_at": iso(now())}
    runtime_path(root, "submissions").mkdir(parents=True, exist_ok=True)
    (root / RUNTIME / "submissions" / f"{args.slot}.json").write_text(json.dumps(sub, indent=2))
    print(f"{args.slot} submitted for Supervisor review ({ahead} commit(s), {len(files)} file(s)).")
    print("ALL DONE SUBMITTED FOR REVIEW AND MERGE")
    return 0


def _transition(root: Path, slot_id: str, status: str, **fields: Any) -> None:
    queue = read_json(root, F_QUEUE)
    slot = _slot(queue, slot_id)
    slot["status"] = status
    slot.update(fields)
    gateway_write(root, F_QUEUE, queue)


def cmd_review(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    supervisor_runtime(root)
    claim = _claim(root, args.slot)
    sub_path = root / RUNTIME / "submissions" / f"{args.slot}.json"
    if not sub_path.exists():
        raise AnposError(f"{args.slot} has no submission to review")
    sub = json.loads(sub_path.read_text())
    if git(Path(claim["worktree"]), "rev-parse", "HEAD") != sub["head_sha"]:
        raise AnposError("the branch changed after submission; ask the worker to submit again")
    bad = path_violations(root, claim, own_changes(root, claim)[0])
    if bad:
        raise AnposError("submission touches forbidden paths:\n  - " + "\n  - ".join(bad))
    slot = _slot(read_json(root, F_QUEUE), args.slot)
    if slot["status"] in {"in_progress", "changes_requested"}:
        _transition(root, args.slot, "submitted_for_review", submitted_at=sub["submitted_at"],
                    review_reference=f"commit:{sub['head_sha']}")
    elif slot["status"] != "submitted_for_review":
        raise AnposError(f"{args.slot} is {slot['status']}, not reviewable")
    if args.approve:
        _transition(root, args.slot, "approved")
        print(f"{args.slot} approved; merge it with `team merge --slot {args.slot}`")
    else:
        queue = read_json(root, F_QUEUE)
        notes = list(_slot(queue, args.slot).get("notes") or []) + [f"changes requested {iso(now())}: {args.changes}"]
        _transition(root, args.slot, "changes_requested", notes=notes)
        sub_path.unlink()
        print(f"{args.slot}: changes requested. Send the worker back to the same worktree.")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    sup = supervisor_runtime(root)
    claim = _claim(root, args.slot)
    queue = read_json(root, F_QUEUE)
    slot = _slot(queue, args.slot)
    if slot.get("status") != "approved":
        raise AnposError(f"{args.slot} is {slot.get('status')}; only approved slots merge")
    sub = json.loads((root / RUNTIME / "submissions" / f"{args.slot}.json").read_text())
    if git(Path(claim["worktree"]), "rev-parse", "HEAD") != sub["head_sha"]:
        raise AnposError("the branch changed after approval; review again")
    current = git(root, "branch", "--show-current", check=False)
    if current != sup.get("base_branch"):
        raise AnposError(f"check out the base branch {sup.get('base_branch')} in the main worktree first")
    dirty = dirty_outside_coordination(root)
    if dirty:
        raise AnposError("the main working tree has uncommitted changes: " + ", ".join(dirty[:5]))
    proc = subprocess.run(["git", "-c", "user.name=ANPOS Supervisor", "-c", "user.email=anpos@localhost",
                           "merge", "--no-ff", claim["branch"], "-m",
                           f"Merge {slot['work_unit_id']} ({args.slot}) via ANPOS Supervisor"],
                          cwd=root, capture_output=True, text=True,
                          env=dict(os.environ, GIT_MERGE_AUTOEDIT="no"))
    if proc.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=root, capture_output=True)
        alerts = read_json(root, F_ALERTS)
        alerts.setdefault("alerts", []).append({"id": f"ALERT-{uuid.uuid4().hex[:8]}", "type": "merge_conflict",
                                                "slot_id": args.slot, "created_at": iso(now()), "status": "open",
                                                "detail": (proc.stdout + proc.stderr)[-400:]})
        gateway_write(root, F_ALERTS, alerts)
        _transition(root, args.slot, "changes_requested",
                    notes=list(slot.get("notes") or []) + ["merge conflict with the base branch: rebase/merge base first"])
        raise AnposError("merge conflict: merge aborted, slot sent back with changes_requested and an alert recorded")
    merge_sha = git(root, "rev-parse", "HEAD")
    _transition(root, args.slot, "merged")
    _transition(root, args.slot, "completed", lease_status="released", released_at=iso(now()))
    events = read_json(root, F_MERGE)
    events.setdefault("events", []).append({"slot_id": args.slot, "work_unit_id": slot["work_unit_id"],
                                            "merge_sha": merge_sha, "head_sha": sub["head_sha"],
                                            "merged_at": iso(now()), "by": sup["agent_id"]})
    gateway_write(root, F_MERGE, events)
    state = read_json(root, F_SUP)
    state["merge_generation"] = int(state.get("merge_generation") or 0) + 1
    state["last_merge_sha"] = merge_sha
    state["last_merge_at"] = iso(now())
    write_json(root, F_SUP, state)
    # work unit completion with evidence (repository canonical)
    plan = read_json(root, core.F_PLAN)
    for unit in plan.get("work_units", []):
        if unit.get("id") == slot["work_unit_id"]:
            unit["status"] = "complete"
            unit.setdefault("completion_evidence", []).extend([f"merge {merge_sha}", sub["evidence"]])
    write_json(root, core.F_PLAN, plan)
    subprocess.run(["git", "worktree", "remove", "--force", claim["worktree"]], cwd=root, capture_output=True)
    subprocess.run(["git", "branch", "-d", claim["branch"]], cwd=root, capture_output=True)
    delete_ref(root, "refs/" + slot["claim_ref"])
    archive = runtime_path(root, "done")
    archive.mkdir(parents=True, exist_ok=True)
    (root / RUNTIME / "claims" / f"{args.slot}.json").rename(archive / f"{args.slot}.claim.json")
    (root / RUNTIME / "submissions" / f"{args.slot}.json").rename(archive / f"{args.slot}.submission.json")
    core.cmd_sync_state(argparse.Namespace(root=str(root)))
    core.audit(root, "slot_merged", args.slot, "merged", actor=sup["agent_id"], role="supervisor", result_ref=merge_sha)
    print(f"{args.slot} merged at {merge_sha[:10]}; {slot['work_unit_id']} complete. Commit the updated ANPOS state files.")
    return 0


def _release(root: Path, slot_id: str, reason: str, reopen: bool) -> None:
    queue = read_json(root, F_QUEUE)
    slot = _slot(queue, slot_id)
    status = slot.get("status")
    if reopen:
        if status == "in_progress" or status == "changes_requested":
            _transition(root, slot_id, "blocked", notes=list(slot.get("notes") or []) + [f"released: {reason}"])
            status = "blocked"
        if status in {"claimed", "blocked"}:
            _transition(root, slot_id, "free")
        queue = read_json(root, F_QUEUE)
        s = _slot(queue, slot_id)
        if s.get("status") == "free":
            s.update({"claim_ref": None, "claim_branch": None, "claimant": None, "claimant_identity_ref": None,
                      "claim_id": None, "claim_nonce": None, "coordination_epoch": None, "fencing_token": None,
                      "lease_status": "not_acquired", "lease_expires_at": None, "released_at": iso(now())})
            gateway_write(root, F_QUEUE, queue)
    else:
        _transition(root, slot_id, "canceled", notes=list(slot.get("notes") or []) + [f"canceled: {reason}"])
    try:
        claim = _claim(root, slot_id)
        subprocess.run(["git", "worktree", "remove", "--force", claim["worktree"]], cwd=root, capture_output=True)
        (root / RUNTIME / "claims" / f"{slot_id}.json").unlink(missing_ok=True)
        (root / RUNTIME / "submissions" / f"{slot_id}.json").unlink(missing_ok=True)
    except AnposError:
        pass
    if slot.get("claim_ref"):
        delete_ref(root, "refs/" + slot["claim_ref"])


def cmd_release(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    supervisor_runtime(root)
    _release(root, args.slot, args.reason, reopen=not args.cancel)
    core.audit(root, "slot_released" if not args.cancel else "slot_canceled", args.slot, args.reason, role="supervisor")
    print(f"{args.slot} {'canceled' if args.cancel else 'released back to free'} (work branch kept for evidence)")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    supervisor_runtime(root)
    queue = read_json(root, F_QUEUE)
    recovered = []
    for s in queue.get("slots", []):
        if s.get("status") in ACTIVE and s.get("lease_expires_at") and parse_time(s["lease_expires_at"]) <= now():
            if s.get("status") in {"submitted_for_review", "approved"}:
                continue  # waiting on the Supervisor, not on the worker
            _release(root, s["id"], "lease expired; recovered by Supervisor", reopen=True)
            recovered.append(s["id"])
    emit({"recovered": recovered}, True)
    return 0


def cmd_team_status(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = read_json(root, F_SUP)
    queue = read_json(root, F_QUEUE)
    limits = (read_json(root, F_BUDGET).get("limits") or {})
    sup = state.get("supervisor") or {}
    rows = [{k: s.get(k) for k in ("id", "work_unit_id", "status", "claimant", "lease_expires_at")}
            for s in queue.get("slots", [])]
    emit({"supervisor": {k: sup.get(k) for k in ("status", "agent_id", "lease_expires_at")},
          "coordination_epoch": state.get("coordination_epoch"), "merge_generation": state.get("merge_generation"),
          "active_slots": sum(1 for s in queue.get("slots", []) if s.get("status") in ACTIVE),
          "max_parallel_agents": limits.get("max_parallel_agents"), "slots": rows,
          "submissions_waiting": sorted(p.stem for p in (root / RUNTIME / "submissions").glob("*.json"))
          if (root / RUNTIME / "submissions").exists() else []}, True)
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    claim = _claim(root, args.slot)
    slot = _slot(read_json(root, F_QUEUE), args.slot)
    text = "\n".join([
        f"You are ANPOS worker `{claim['agent_id']}` for slot {args.slot} ({slot.get('work_unit_id')}): {slot.get('title')}.",
        f"Work only in `{claim['worktree']}` on branch `{claim['branch']}`. Do not push, merge or touch other branches.",
        f"Objective: {slot.get('description')}",
        "Allowed paths: " + ", ".join(claim["allowed_paths"]) + (". Denied: " + ", ".join(claim["denied_paths"]) if claim["denied_paths"] else ""),
        "Acceptance criteria:", *[f"- {c}" for c in slot.get("acceptance_criteria") or []],
        "Required checks (run them all):", *[f"- {c}" for c in slot.get("required_checks") or []],
        "Commit your work on the branch. Then report which checks ran and their results.",
        "Never edit ANPOS control-plane files (AGENTS.md, .ai/, config/, schemas/, scripts/, .github/).",
        "If a check keeps failing the same way, stop after 3 attempts and report the blocker instead of looping.",
    ])
    print(text)
    return 0


ACTIONS = {"agents_select": _apply_agents_select, "budgets_set": _apply_budgets}


# ---------------------------------------------------------------- CLI registration

def register(sub) -> None:
    ag = sub.add_parser("agents", help="Development AI selection").add_subparsers(dest="agents_cmd", required=True)
    ag.add_parser("detect", help="find agents this host can actually run").set_defaults(func=cmd_agents_detect)
    s = ag.add_parser("select", help="propose the agent pool (APPROVE CHANGE)")
    s.add_argument("--supervisor")
    s.add_argument("--worker", action="append")
    s.add_argument("--allowed-path", action="append", help="permission profile allowed paths (default **)")
    s.add_argument("--denied-path", action="append")
    s.add_argument("--ttl-days", type=int, default=30, help="identity evidence validity")
    s.set_defaults(func=cmd_agents_select)
    ag.add_parser("list", help="selected agents").set_defaults(func=cmd_agents_list)

    tm = sub.add_parser("team", help="Supervisor/Worker coordination").add_subparsers(dest="team_cmd", required=True)
    s = tm.add_parser("supervisor", help="acquire | heartbeat | release the Supervisor lease")
    s.add_argument("action", choices=["acquire", "heartbeat", "release"])
    s.add_argument("--agent-id", default="claude-supervisor")
    s.add_argument("--lease-minutes", type=int, default=60)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_supervisor)
    tm.add_parser("slots", help="create work-queue slots from the approved plan").set_defaults(func=cmd_slots)
    s = tm.add_parser("dispatch", help="claim a slot for a worker and create its worktree")
    s.add_argument("--agent-id", default="claude-worker")
    s.add_argument("--slot")
    s.add_argument("--lease-minutes", type=int, default=90)
    s.set_defaults(func=cmd_dispatch)
    s = tm.add_parser("brief", help="worker instructions for a claimed slot (for subagents or other CLIs)")
    s.add_argument("--slot", required=True)
    s.set_defaults(func=cmd_brief)
    s = tm.add_parser("heartbeat", help="renew a worker lease")
    s.add_argument("--slot", required=True)
    s.add_argument("--agent-id", required=True)
    s.add_argument("--lease-minutes", type=int, default=90)
    s.set_defaults(func=cmd_heartbeat)
    s = tm.add_parser("submit", help="worker: submit committed work for review")
    s.add_argument("--slot", required=True)
    s.add_argument("--agent-id", required=True)
    s.add_argument("--evidence")
    s.set_defaults(func=cmd_submit)
    s = tm.add_parser("review", help="Supervisor: approve or request changes")
    s.add_argument("--slot", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--changes", metavar="WHAT")
    s.set_defaults(func=cmd_review)
    s = tm.add_parser("merge", help="Supervisor: merge an approved slot and complete its work unit")
    s.add_argument("--slot", required=True)
    s.set_defaults(func=cmd_merge)
    s = tm.add_parser("release", help="Supervisor: put a slot back to free (or --cancel)")
    s.add_argument("--slot", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--cancel", action="store_true")
    s.set_defaults(func=cmd_release)
    tm.add_parser("recover", help="Supervisor: reopen slots whose worker lease expired").set_defaults(func=cmd_recover)
    tm.add_parser("status", help="Supervisor, slots, budgets").set_defaults(func=cmd_team_status)
    bd = tm.add_parser("budgets", help="runtime budgets").add_subparsers(dest="budget_cmd", required=True)
    bd.add_parser("show").set_defaults(func=cmd_budgets_show)
    s = bd.add_parser("propose", help="change limits (APPROVE CHANGE)")
    for field, typ in BUDGET_FIELDS.items():
        s.add_argument("--" + field.replace("_", "-"), dest=field, type=typ)
    s.set_defaults(func=cmd_budgets_propose)
