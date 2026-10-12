"""Tests for Development AI selection, Supervisor/Worker coordination, budgets and the circuit breaker."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_anpos import CAPTURE, GUARD, PLUGIN, hook, load, run, save, write_plan
from test_full_mode import approve_with, doc, edit, git, new_repo

BREAKER = PLUGIN / "hooks" / "breaker.py"


def approve_change(root: Path, *args: str) -> dict:
    out = json.loads(run(root, *args).stdout)
    assert out["approve_phrase"].startswith("APPROVE CHANGE")
    hook(CAPTURE, {"prompt": out["approve_phrase"], "session_id": "s"}, root)
    run(root, "consent", "confirm")
    return out


@pytest.fixture()
def team_repo(tmp_path: Path) -> Path:
    repo = new_repo(tmp_path / "team")
    run(repo, "init", "--full")
    run(repo, "evidence", "add", "--claim", "c", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "low", "--summary", "s")
    write_plan(repo)
    approve_with(repo, "plan_approval", kind="PLAN")
    reach_development(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "approved plan")
    return repo


def reach_development(repo: Path) -> None:
    """Walk Stages 8-12 with minimal real outputs so team mode (Stage 13) is allowed."""
    run(repo, "stage", "system_design")
    doc(repo, "SYSTEM-DESIGN.md")
    edit(repo, "config/security/threat-model.json", lambda d: d.update(
        status="active", assets=[{"id": "A1"}], actors=[{"id": "U1"}], entry_points=[{"id": "E1"}],
        threats=[{"id": "T1", "impact": "low", "status": "open"}]))
    edit(repo, "config/data/data-governance.json", lambda d: d.update(status="active", data_records=[],
                                                                      data_scope_note="no stored data"))
    approve_with(repo, "technology_stack", "--change", "Python 3.12", kind="STACK")
    doc(repo, "ARCHITECTURE.md")
    edit(repo, "config/architecture/decision-records.json",
         lambda d: d["decisions"].append(dict(d["decision_template"], id="ADR-0001", title="Monolith", status="accepted")))
    run(repo, "stage", "data_flow")
    run(repo, "stage", "ui_ux", "--not-applicable", "library")
    run(repo, "stage", "development", "--not-applicable", "no UI")


def setup_team(repo: Path) -> None:
    detected = json.loads(run(repo, "agents", "detect").stdout)
    assert {"claude-supervisor", "claude-worker"} <= {a["id"] for a in detected["available"]}
    approve_change(repo, "agents", "select", "--supervisor", "claude-supervisor", "--worker", "claude-worker")
    catalog = load(repo, "config/ai/agent-catalog.json")
    assert catalog["role_assignments"]["supervisor_agent_id"] == "claude-supervisor"
    assert all(a["identity_verified"] for a in catalog["selected_agents"])
    assert (repo / ".anpos/identity/claude-worker.json").exists()
    run(repo, "team", "supervisor", "acquire")
    slots = json.loads(run(repo, "team", "slots").stdout)
    assert slots["added_slots"] == ["SLOT-WU-AUTH-1", "SLOT-WU-TASK-1"]


def worker_commit(repo: Path, brief: dict, rel: str, text: str = "x = 1\n") -> None:
    wt = Path(brief["worktree"])
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(text)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", f"work on {brief['work_unit_id']}")


def test_agent_pool_needs_human_approval(team_repo: Path) -> None:
    out = json.loads(run(team_repo, "agents", "select", "--supervisor", "claude-supervisor", "--worker", "claude-worker").stdout)
    assert load(team_repo, "config/ai/agent-catalog.json")["selected_agents"] == []
    assert "no user decision" in run(team_repo, "consent", "confirm", ok=False).stderr
    hook(CAPTURE, {"prompt": out["approve_phrase"]}, team_repo)
    run(team_repo, "consent", "confirm")
    req = load(team_repo, "config/consent/consent-requests.json")["requests"][-1]
    assert req["status"] == "implemented" and "agents_select" in req["implementation_reference"]
    assert "not in the selected" in run(team_repo, "team", "supervisor", "acquire", "--agent-id", "codex-worker", ok=False).stderr


def test_full_supervisor_worker_cycle(team_repo: Path) -> None:
    repo = team_repo
    setup_team(repo)
    assert "explicit cost limits" in run(repo, "team", "dispatch", ok=False).stderr
    approve_change(repo, "team", "budgets", "propose", "--token-budget", "1000000", "--model-cost-budget", "20",
                   "--max-parallel-agents", "2")
    assert load(repo, "config/runtime/budgets.json")["limits"]["token_budget"] == 1000000

    brief = json.loads(run(repo, "team", "dispatch").stdout)
    assert brief["slot_id"] == "SLOT-WU-AUTH-1" and Path(brief["worktree"]).is_dir()
    assert git(Path(brief["worktree"]), "branch", "--show-current") == "anpos/WU-AUTH-1"
    assert "no dependency-satisfied" in run(repo, "team", "dispatch", ok=False).stderr  # TASK waits for AUTH
    assert "no dependency-satisfied" in run(repo, "team", "dispatch", "--slot", "SLOT-WU-AUTH-1", ok=False).stderr
    slot = [s for s in load(repo, "config/coordination/agent-work-queue.json")["slots"] if s["id"] == "SLOT-WU-AUTH-1"][0]
    assert slot["status"] == "in_progress" and slot["claimant"] == "claude-worker"
    assert "0 error(s)" in run(repo, "validate").stdout  # upstream validator accepts the live claim

    # the worker strays outside its paths -> submission refused
    worker_commit(repo, brief, "config/hack.json", "{}")
    assert "outside" in run(repo, "team", "submit", "--slot", "SLOT-WU-AUTH-1", "--agent-id", "claude-worker",
                             "--evidence", "x", ok=False).stderr
    wt = Path(brief["worktree"])
    git(wt, "rm", "-q", "config/hack.json")
    git(wt, "commit", "-qm", "undo")
    worker_commit(repo, brief, "src/auth.py")
    assert "--evidence" in run(repo, "team", "submit", "--slot", "SLOT-WU-AUTH-1", "--agent-id", "claude-worker",
                               ok=False).stderr
    assert "claimed by claude-worker" in run(repo, "team", "submit", "--slot", "SLOT-WU-AUTH-1", "--agent-id", "other",
                                             "--evidence", "x", ok=False).stderr
    out = run(repo, "team", "submit", "--slot", "SLOT-WU-AUTH-1", "--agent-id", "claude-worker",
              "--evidence", "pytest: 4 passed").stdout
    assert "ALL DONE SUBMITTED FOR REVIEW AND MERGE" in out

    run(repo, "team", "review", "--slot", "SLOT-WU-AUTH-1", "--approve")
    run(repo, "team", "merge", "--slot", "SLOT-WU-AUTH-1")
    assert (repo / "src/auth.py").exists()
    unit = [w for w in load(repo, "config/ai/execution-plan.json")["work_units"] if w["id"] == "WU-AUTH-1"][0]
    assert unit["status"] == "complete" and any(e.startswith("merge ") for e in unit["completion_evidence"])
    assert load(repo, "config/coordination/supervisor-state.json")["merge_generation"] == 1
    assert not Path(brief["worktree"]).exists()

    # next slot becomes claimable; changes requested then approved
    brief2 = json.loads(run(repo, "team", "dispatch").stdout)
    assert brief2["slot_id"] == "SLOT-WU-TASK-1"
    worker_commit(repo, brief2, "src/tasks.py")
    run(repo, "team", "submit", "--slot", "SLOT-WU-TASK-1", "--agent-id", "claude-worker", "--evidence", "ok")
    run(repo, "team", "review", "--slot", "SLOT-WU-TASK-1", "--changes", "add tests")
    worker_commit(repo, brief2, "src/test_tasks.py")
    run(repo, "team", "submit", "--slot", "SLOT-WU-TASK-1", "--agent-id", "claude-worker", "--evidence", "tests added")
    run(repo, "team", "review", "--slot", "SLOT-WU-TASK-1", "--approve")
    run(repo, "team", "merge", "--slot", "SLOT-WU-TASK-1")
    status = json.loads(run(repo, "team", "status").stdout)
    assert [s["status"] for s in status["slots"]] == ["completed", "completed"]
    assert "0 error(s)" in run(repo, "validate").stdout
    run(repo, "team", "supervisor", "release")


def test_parallel_budget_lease_expiry_and_recovery(team_repo: Path) -> None:
    repo = team_repo
    plan = load(repo, "config/ai/execution-plan.json")
    plan["work_units"][1]["dependencies"] = []  # make both slots independent
    save(repo, "config/ai/execution-plan.json", plan)
    setup_team(repo)
    approve_change(repo, "team", "budgets", "propose", "--token-budget", "1", "--model-cost-budget", "1",
                   "--max-parallel-agents", "1")
    run(repo, "team", "dispatch")
    assert "parallel-agent budget" in run(repo, "team", "dispatch", ok=False).stderr
    queue = load(repo, "config/coordination/agent-work-queue.json")
    active = [s for s in queue["slots"] if s["status"] == "in_progress"][0]
    active["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    save(repo, "config/coordination/agent-work-queue.json", queue)
    assert "expired" in run(repo, "team", "heartbeat", "--slot", active["id"], "--agent-id", "claude-worker",
                             ok=False).stderr
    recovered = json.loads(run(repo, "team", "recover").stdout)["recovered"]
    assert recovered == [active["id"]]
    slot = [s for s in load(repo, "config/coordination/agent-work-queue.json")["slots"] if s["id"] == active["id"]][0]
    assert slot["status"] == "free" and slot["claimant"] is None
    run(repo, "team", "dispatch")  # capacity is free again


def test_parallel_workers_can_merge_base_and_only_own_changes_are_reviewed(team_repo: Path) -> None:
    repo = team_repo
    plan = load(repo, "config/ai/execution-plan.json")
    plan["work_units"][1]["dependencies"] = []
    save(repo, "config/ai/execution-plan.json", plan)
    setup_team(repo)
    approve_change(repo, "team", "budgets", "propose", "--token-budget", "1", "--model-cost-budget", "1",
                   "--max-parallel-agents", "2")
    first = json.loads(run(repo, "team", "dispatch").stdout)
    second = json.loads(run(repo, "team", "dispatch").stdout)
    assert {first["slot_id"], second["slot_id"]} == {"SLOT-WU-AUTH-1", "SLOT-WU-TASK-1"}
    worker_commit(repo, first, "src/shared.py")
    run(repo, "team", "submit", "--slot", first["slot_id"], "--agent-id", "claude-worker", "--evidence", "ok")
    run(repo, "team", "review", "--slot", first["slot_id"], "--approve")
    run(repo, "team", "merge", "--slot", first["slot_id"])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "state")
    wt = Path(second["worktree"])
    base_branch = second["base_branch"]
    git(wt, "merge", "-q", "--no-edit", base_branch)  # pick up the merged work
    worker_commit(repo, second, "src/second.py")
    run(repo, "team", "submit", "--slot", second["slot_id"], "--agent-id", "claude-worker", "--evidence", "ok")
    sub = json.loads((repo / ".anpos/runtime/submissions" / f"{second['slot_id']}.json").read_text())
    assert sub["files"] == ["src/second.py"]
    run(repo, "team", "review", "--slot", second["slot_id"], "--approve")
    run(repo, "team", "merge", "--slot", second["slot_id"])
    assert (repo / "src/second.py").exists() and (repo / "src/shared.py").exists()


def test_supervisor_lease_is_exclusive_and_fenced(team_repo: Path) -> None:
    repo = team_repo
    setup_team(repo)
    assert "active Supervisor lease" in run(repo, "team", "supervisor", "acquire", ok=False).stderr
    state = load(repo, "config/coordination/supervisor-state.json")
    state["supervisor"]["fencing_token"] = "supervisor:99:stolen"
    save(repo, "config/coordination/supervisor-state.json", state)
    assert "no longer live" in run(repo, "team", "slots", ok=False).stderr


def test_guard_keeps_workers_inside_their_worktree(team_repo: Path) -> None:
    repo = team_repo
    setup_team(repo)
    approve_change(repo, "team", "budgets", "propose", "--token-budget", "1", "--model-cost-budget", "1")
    brief = json.loads(run(repo, "team", "dispatch").stdout)
    wt = brief["worktree"]
    ok = {"tool_name": "Write", "tool_input": {"file_path": f"{wt}/src/a.py", "content": "x"}}
    assert hook(GUARD, ok, repo).returncode == 0
    outside = {"tool_name": "Write", "tool_input": {"file_path": f"{wt}/docs/a.md", "content": "x"}}
    assert hook(GUARD, outside, repo).returncode == 2
    protected = {"tool_name": "Edit", "tool_input": {"file_path": f"{wt}/config/ai/agent-catalog.json",
                                                     "old_string": "a", "new_string": "b"}}
    assert hook(GUARD, protected, repo).returncode == 2
    worker_main = {"tool_name": "Write", "agent_type": "anpos:worker",
                   "tool_input": {"file_path": f"{repo}/src/main.py", "content": "x"}}
    assert hook(GUARD, worker_main, repo).returncode == 2
    main_session = {"tool_name": "Write", "tool_input": {"file_path": f"{repo}/src/main.py", "content": "x"}}
    assert hook(GUARD, main_session, repo).returncode == 0


def test_protected_paths_in_plan_are_flagged(team_repo: Path) -> None:
    plan = load(team_repo, "config/ai/execution-plan.json")
    plan["work_units"][0]["allowed_paths"] = ["tests/**"]
    save(team_repo, "config/ai/execution-plan.json", plan)
    assert "overlaps protected ANPOS path" in run(team_repo, "validate").stdout


def test_team_mode_requires_development_stage(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "early")
    run(repo, "init", "--full")
    run(repo, "evidence", "add", "--claim", "c", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "low", "--summary", "s")
    write_plan(repo)
    approve_with(repo, "plan_approval", kind="PLAN")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "plan")
    approve_change(repo, "agents", "select", "--supervisor", "claude-supervisor", "--worker", "claude-worker")
    run(repo, "team", "supervisor", "acquire")
    assert "Stage 13" in run(repo, "team", "slots", ok=False).stderr


def test_team_mode_requires_full_child(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "lite")
    run(repo, "init")
    assert "full ANPOS child" in run(repo, "agents", "detect", ok=False).stderr


def test_circuit_breaker(team_repo: Path) -> None:
    repo = team_repo
    fail = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "npm test"},
            "cwd": str(repo)}
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "npm test"}, "cwd": str(repo)}
    limit = load(repo, "config/runtime/budgets.json")["limits"]["max_same_error_retries"]
    for _ in range(limit):
        assert hook(BREAKER, pre, repo).returncode == 0
        hook(BREAKER, fail, repo)
    blocked = hook(BREAKER, pre, repo)
    assert blocked.returncode == 2 and "circuit breaker" in blocked.stderr.lower()
    other = dict(pre, tool_input={"command": "npm run lint"})
    assert hook(BREAKER, other, repo).returncode == 0
    run(repo, "breaker", "reset")
    assert hook(BREAKER, pre, repo).returncode == 0
    hook(BREAKER, fail, repo)
    hook(BREAKER, {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "npm test"},
                   "cwd": str(repo)}, repo)  # success clears the count
    assert json.loads(run(repo, "breaker", "status").stdout) == {}
    outside = tmp_path_outside = repo.parent / "nonanpos"
    outside.mkdir()
    for _ in range(limit + 1):
        hook(BREAKER, dict(fail, cwd=str(outside)), outside)
    assert hook(BREAKER, dict(pre, cwd=str(outside)), outside).returncode == 0
