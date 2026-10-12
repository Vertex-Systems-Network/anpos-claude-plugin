"""Tests for full ANPOS mode (bootstrap/adoption), PM sync and Stage 8-17 gates."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_anpos import CAPTURE, CLI, PLUGIN, hook, load, run, save, write_plan

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(root: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def new_repo(path: Path, remote: str = "https://github.com/acme/app.git") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "remote", "add", "origin", remote)
    return path


def approve_with(root: Path, ctype: str, *extra: str, kind: str) -> dict:
    out = json.loads(run(root, "consent", "propose", "--type", ctype, *extra).stdout)
    hook(CAPTURE, {"prompt": f"APPROVE {kind} {out['approval_code']}", "session_id": "s"}, root)
    run(root, "consent", "confirm")
    return out


@pytest.fixture()
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    script = bindir / "gh"
    script.write_text(f"#!/bin/sh\nexec {sys.executable} {PLUGIN / 'tests' / 'fake_gh.py'} \"$@\"\n")
    script.chmod(0o755)
    state = tmp_path / "ghstate.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    return state


# ---------------------------------------------------------------- full mode

def test_full_install_on_empty_repo_passes_upstream_validator(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "app")
    dry = json.loads(run(repo, "init", "--full", "--dry-run").stdout)
    assert dry["state"] == "empty_repository" and dry["summary"]["keep_project"] == 0
    run(repo, "init", "--full")
    assert json.loads(run(repo, "detect").stdout)["state"] == "active_project"
    assert load(repo, "config/protocol/instance.json")["repository"] == "acme/app"
    assert not (repo / "commercial-service").exists()
    out = run(repo, "validate").stdout
    assert "0 error(s)" in out
    assert load(repo, ".anpos/planner.json")["mode"] == "full"


def test_adoption_never_overwrites_project_files(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "shop", "git@github.com:acme/shop.git")
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / "README.md").write_text("# Shop\n")
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / ".github/CODEOWNERS").write_text("* @shopteam\n")
    (repo / ".github/dependabot.yml").write_text("version: 2\nupdates: []\n")
    (repo / ".github/workflows/ci.yml").write_text("name: ci\non: push\njobs: {}\n")
    (repo / "CLAUDE.md").write_text("# Rules\nUse pnpm.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    (repo / "scratch.txt").write_text("dirty")
    assert "uncommitted" in run(repo, "init", "--full", ok=False).stderr
    (repo / "scratch.txt").unlink()

    result = json.loads(run(repo, "init", "--full").stdout)
    assert result["branch"] == "anpos/adopt" and git(repo, "branch", "--show-current") == "anpos/adopt"
    assert (repo / "README.md").read_text() == "# Shop\n"
    assert (repo / ".github/CODEOWNERS").read_text() == "* @shopteam\n"
    assert (repo / ".github/dependabot.yml").read_text() == "version: 2\nupdates: []\n"
    assert (repo / ".github/workflows/ci.yml").read_text().startswith("name: ci")
    assert (repo / ".gitignore").read_text().startswith("node_modules/\n") and "added by ANPOS" in (repo / ".gitignore").read_text()
    claude = (repo / "CLAUDE.md").read_text()
    assert claude.startswith("# Rules\nUse pnpm.") and "ANPOS:BEGIN" in claude
    assert (repo / "docs/anpos/adoption/README.md.anpos").exists()
    assert (repo / "docs/anpos/adoption/.github/CODEOWNERS.anpos").exists()
    assert "README.md" in (repo / "docs/anpos/ADOPTION-REPORT.md").read_text()
    out = run(repo, "validate").stdout
    assert "0 error(s)" in out and "adoption follow-up" in out


def test_lite_to_full_upgrade_keeps_plan_and_approval(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "lite")
    run(repo, "init")
    run(repo, "evidence", "add", "--claim", "c", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "low", "--summary", "s")
    write_plan(repo)
    approve_with(repo, "plan_approval", kind="PLAN")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lite plan")
    run(repo, "init", "--full")
    assert json.loads(run(repo, "detect").stdout)["state"] == "active_project"
    plan = load(repo, "config/ai/execution-plan.json")
    assert plan["plan_status"] == "approved" and len(plan["work_units"]) == 2
    assert len(load(repo, "config/research/evidence-registry.json")["entries"]) == 1
    assert "0 error(s)" in run(repo, "validate").stdout


def test_full_mode_refuses_canonical_and_needs_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "git repository" in run(plain, "init", "--full", ok=False).stderr


# ---------------------------------------------------------------- PM sync

def approved_repo(tmp_path: Path, remote: str = "https://github.com/acme/app.git") -> Path:
    repo = new_repo(tmp_path / "pm", remote)
    run(repo, "init")
    run(repo, "evidence", "add", "--claim", "c", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "low", "--summary", "s")
    write_plan(repo)
    approve_with(repo, "plan_approval", kind="PLAN")
    return repo


def test_github_sync_is_idempotent_and_repository_stays_canonical(tmp_path: Path, fake_gh: Path) -> None:
    repo = approved_repo(tmp_path)
    run(repo, "pm", "select", "github_projects")
    plan = json.loads(run(repo, "pm", "plan").stdout)
    assert plan["counts"] == {"create": 3, "update": 0, "archive": 0}
    run(repo, "pm", "apply")
    gh = json.loads(fake_gh.read_text())
    assert len(gh["issues"]) == 2 and len(gh["milestones"]) == 1
    assert all(i["milestone"] == "PHASE-1 — MVP" for i in gh["issues"].values())
    assert json.loads(run(repo, "pm", "plan").stdout)["counts"] == {"create": 0, "update": 0, "archive": 0}

    # a lost mapping does not duplicate issues: the idempotency marker is found again
    state = load(repo, "config/integrations/pm-sync-state.json")
    state["items"].pop("WU-AUTH-1")
    save(repo, "config/integrations/pm-sync-state.json", state)
    run(repo, "pm", "apply")
    assert len(json.loads(fake_gh.read_text())["issues"]) == 2

    # repository completion closes the issue
    run(repo, "wu", "WU-AUTH-1", "complete", "--evidence", "tests pass")
    run(repo, "pm", "apply")
    gh = json.loads(fake_gh.read_text())
    number = load(repo, "config/integrations/pm-sync-state.json")["items"]["WU-AUTH-1"]["external_id"]
    assert gh["issues"][number]["state"] == "CLOSED"
    pulled = json.loads(run(repo, "pm", "pull").stdout)
    assert pulled["new_conflicts"] == []  # our own close is not a conflict

    # someone closes an unfinished work unit's issue in GitHub -> conflict, plan untouched
    other = load(repo, "config/integrations/pm-sync-state.json")["items"]["WU-TASK-1"]["external_id"]
    gh["issues"][other].update(state="CLOSED", updatedAt="2026-10-12T09:00:00Z")
    fake_gh.write_text(json.dumps(gh))
    pulled = json.loads(run(repo, "pm", "pull").stdout)
    assert pulled["new_conflicts"][0]["item_id"] == "WU-TASK-1"
    assert pulled["new_conflicts"][0]["authority"] == "repository"
    unit = [w for w in load(repo, "config/ai/execution-plan.json")["work_units"] if w["id"] == "WU-TASK-1"][0]
    assert unit["status"] == "not_started"
    assert json.loads(run(repo, "pm", "pull").stdout)["unchanged"] >= 1  # same revision is skipped
    cid = pulled["new_conflicts"][0]["id"]
    assert "--resolution" in run(repo, "pm", "conflicts", "--resolve", cid, ok=False).stderr
    run(repo, "pm", "conflicts", "--resolve", cid, "--resolution", "Reopened: work not done in repo")
    assert json.loads(run(repo, "pm", "conflicts").stdout) == []


def test_connector_provider_flow_and_switching(tmp_path: Path) -> None:
    repo = approved_repo(tmp_path)
    assert "no project management provider" in run(repo, "pm", "plan", ok=False).stderr
    run(repo, "pm", "select", "linear", "--workspace-name", "Acme", "--project-id", "P1",
        "--project-name", "App", "--verified")
    plan = json.loads(run(repo, "pm", "plan", "--json").stdout)
    assert plan["counts"]["create"] == 3 and "<!-- anpos:" in plan["actions"][1]["body"]
    assert "pm apply" not in plan["how"]
    assert "drives GitHub" in run(repo, "pm", "apply", ok=False).stderr
    for i, action in enumerate(plan["actions"]):
        run(repo, "pm", "record", action["id"], "--external-id", f"LIN-{i}", "--url", f"https://linear.app/x/{i}",
            "--revision", "r1")
    assert json.loads(run(repo, "pm", "plan").stdout)["counts"]["create"] == 0
    states = tmp_path / "states.json"
    states.write_text(json.dumps([{"external_id": "LIN-1", "state": "done", "revision": "r2"}]))
    pulled = json.loads(run(repo, "pm", "pull", "--from-json", str(states)).stdout)
    assert pulled["new_conflicts"][0]["item_id"] == "WU-AUTH-1"
    assert "--switch" in run(repo, "pm", "select", "jira", ok=False).stderr
    run(repo, "pm", "select", "jira", "--switch")
    state = load(repo, "config/integrations/pm-sync-state.json")
    assert state["provider_id"] == "jira" and state["previous_providers"][0]["provider_id"] == "linear"
    run(repo, "pm", "skip")
    assert load(repo, "config/integrations/pm-sync-state.json")["mode"] == "skipped"


def test_pm_selection_is_mirrored_into_full_child(tmp_path: Path, fake_gh: Path) -> None:
    repo = new_repo(tmp_path / "full")
    run(repo, "init", "--full")
    run(repo, "pm", "select", "linear", "--project-name", "App", "--project-id", "P1", "--verified")
    sel = load(repo, "config/integrations/project-management.json")["selection"]
    assert sel["selected_provider_id"] == "linear" and sel["sync_enabled"] is True
    lin = load(repo, "config/integrations/linear-sync.json")
    assert lin["enabled"] is True and lin["project"]["id"] == "P1"
    assert "0 error(s)" in run(repo, "validate").stdout


# ---------------------------------------------------------------- Stage 8-17 gates

def doc(repo: Path, name: str) -> None:
    (repo / "docs/ai").mkdir(parents=True, exist_ok=True)
    (repo / "docs/ai" / name).write_text(f"# {name}\n\n" + "Real stage output with decisions and evidence. " * 10)


def edit(repo: Path, rel: str, fn) -> None:
    data = load(repo, rel)
    fn(data)
    save(repo, rel, data)


def test_full_lifecycle_gates(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "life")
    run(repo, "init", "--full")
    run(repo, "evidence", "add", "--claim", "c", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "low", "--summary", "s")
    write_plan(repo)
    approve_with(repo, "plan_approval", kind="PLAN")
    run(repo, "stage", "system_design")
    assert run(repo, "stage", "architecture", ok=False).returncode == 1  # in order + gates

    problems = json.loads(run(repo, "stage-check", "--json", ok=False).stdout)["problems"]
    assert any("threat-model" in p for p in problems) and any("data_records" in p for p in problems)
    doc(repo, "SYSTEM-DESIGN.md")
    edit(repo, "config/security/threat-model.json", lambda d: d.update(
        status="active", assets=[{"id": "A1"}], actors=[{"id": "U1"}], entry_points=[{"id": "E1"}],
        threats=[{"id": "T1", "impact": "high", "status": "open"}]))
    edit(repo, "config/data/data-governance.json", lambda d: d.update(
        status="active", data_records=[{"id": "D1", "classification": "confidential", "retention": None,
                                        "deletion_method": None}]))
    approve_with(repo, "technology_stack", "--change", "Backend: FastAPI", kind="STACK")
    assert load(repo, "config/ai/project-state.json")["lifecycle_stage"] == "architecture"

    assert "accepted architecture decision" in run(repo, "stage", "data_flow", ok=False).stderr
    doc(repo, "ARCHITECTURE.md")
    edit(repo, "config/architecture/decision-records.json",
         lambda d: d["decisions"].append(dict(d["decision_template"], id="ADR-0001", title="Monolith", status="accepted")))
    run(repo, "stage", "data_flow")
    doc(repo, "DATA-FLOW.md")
    assert "retention" in run(repo, "stage", "ui_ux", ok=False).stderr
    edit(repo, "config/data/data-governance.json", lambda d: d["data_records"][0].update(
        retention="2 years", deletion_method="hard delete"))
    run(repo, "stage", "ui_ux")
    assert "--not-applicable" in run(repo, "stage", "development", ok=False).stderr
    run(repo, "stage", "development", "--not-applicable", "API only, no UI")
    assert "unfinished work units" in run(repo, "stage", "sqa", ok=False).stderr
    run(repo, "wu", "WU-AUTH-1", "complete", "--evidence", "pytest ok")
    run(repo, "wu", "WU-TASK-1", "complete", "--evidence", "pytest ok")
    run(repo, "stage", "sqa")
    doc(repo, "QA-REPORT.md")
    assert "qa_readiness" in run(repo, "stage", "security", ok=False).stderr
    edit(repo, "config/ai/project-state.json", lambda d: d.update(qa_readiness="ready"))
    run(repo, "stage", "security")
    doc(repo, "SECURITY-REVIEW.md")
    edit(repo, "config/ai/project-state.json", lambda d: d.update(security_readiness="ready"))
    assert "T1" in run(repo, "stage", "release", ok=False).stderr
    edit(repo, "config/security/threat-model.json", lambda d: d["threats"][0].update(status="mitigated"))
    run(repo, "stage", "release")
    assert "operations" not in load(repo, "config/ai/project-state.json")["lifecycle_stage"]
    doc(repo, "RELEASE.md")
    edit(repo, "config/release/release-policy.json", lambda d: d.update(environments=[dict(d["environment_template"], name="prod")]))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "pre-assurance")
    assert "classify applicability" in run(repo, "consent", "propose", "--type", "release_candidate", ok=False).stderr
    for n in range(83, 97):
        req = f"REQ-{n}"
        if req in ("REQ-91", "REQ-96"):
            run(repo, "assurance", "classify", req, "applicable")
            run(repo, "assurance", "evidence", req, "--ref", "ADR-0001" if req == "REQ-91" else "config/risk/risk-register.json#RISK-0001")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", f"evidence {req}")
            run(repo, "assurance", "set-state", req, "passed")
        else:
            run(repo, "assurance", "classify", req, "not_applicable", "--reason", "small internal API, no AI, no personal data")
    assert run(repo, "stage", "operations", ok=False).returncode == 1
    assert "commit" in run(repo, "consent", "propose", "--type", "release_candidate", ok=False).stderr
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "release candidate")
    rc = approve_with(repo, "release_candidate", "--change", "notes: v1", kind="RELEASE")
    assert rc["approve_phrase"].startswith("APPROVE RELEASE")
    assert load(repo, "config/ai/project-state.json")["lifecycle_stage"] == "operations"
    doc(repo, "OPERATIONS.md")
    assert "rto" in run(repo, "stage", "maintenance", ok=False).stderr
    edit(repo, "config/operations/operations-policy.json", lambda d: d["recovery"].update(rto="4h", rpo="1h"))
    run(repo, "stage", "maintenance")
    log = load(repo, ".anpos/stage-log.json")["entries"]
    assert [e["to"] for e in log][-1] == "maintenance"
    assert any(e["exit_check"] and e["exit_check"]["result"] == "not_applicable" for e in log)
    assert "0 error(s)" in run(repo, "validate").stdout
