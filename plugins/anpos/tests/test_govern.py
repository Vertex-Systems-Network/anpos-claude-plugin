"""Tests for VCS host selection, branch rules (auto or guide), Code Quality, assurance, audit journal,
memory provenance, continuous improvement and host-authenticated approvals."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from test_anpos import CAPTURE, PLUGIN, hook, load, run, save, write_plan
from test_full_mode import approve_with, git, new_repo
from test_team import approve_change


def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, script: str, env: str) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / name
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {PLUGIN / 'tests' / script} \"$@\"\n")
    exe.chmod(0o755)
    state = tmp_path / f"{name}-state.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(env, str(state))
    return state


@pytest.fixture()
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return fake_cli(tmp_path, monkeypatch, "gh", "fake_gh.py", "FAKE_GH_STATE")


@pytest.fixture()
def glab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return fake_cli(tmp_path, monkeypatch, "glab", "fake_glab.py", "FAKE_GLAB_STATE")


def full_repo(tmp_path: Path, remote: str) -> Path:
    repo = new_repo(tmp_path / "gov", remote)
    run(repo, "init", "--full")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "anpos")
    return repo


def state(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def put(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------- hosts and rules

def test_vcs_detect_and_select(tmp_path: Path, gh: Path) -> None:
    repo = full_repo(tmp_path, "git@github.com:acme/app.git")
    detected = json.loads(run(repo, "vcs", "detect").stdout)
    assert detected["suggested_host"] == "github"
    hosts = {h["id"]: h for h in detected["hosts"]}
    assert hosts["github"]["automation"] == "auto" and hosts["bitbucket"]["automation"] == "guide"
    run(repo, "vcs", "select", "github")
    assert load(repo, "config/integrations/vcs-host.json")["repository"] == "acme/app"
    assert "rules" in run(repo, "rules", "plan", ok=True).stdout


def test_github_rules_applied_verified_and_drift(tmp_path: Path, gh: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    put(gh, {"check_runs": [{"name": "repository-integrity", "conclusion": "success"},
                            {"name": "flaky", "conclusion": "failure"}]})
    run(repo, "vcs", "select", "github")
    plan = json.loads(run(repo, "rules", "plan", "--profile", "solo").stdout)
    assert plan["rules"]["required_status_checks"] == ["repository-integrity"]
    assert plan["rules"]["required_approving_reviews"] == 0
    approve_change(repo, "rules", "propose", "--profile", "team")
    gh_state = state(gh)
    ruleset = list(gh_state["rulesets"].values())[0]
    types = {r["type"]: r.get("parameters") for r in ruleset["rules"]}
    assert {"deletion", "non_fast_forward", "required_linear_history", "pull_request", "required_status_checks"} <= set(types)
    assert types["pull_request"]["required_approving_review_count"] == 1
    assert types["required_status_checks"]["required_status_checks"] == [{"context": "repository-integrity"}]
    assert gh_state["repo"]["allow_merge_commit"] is False and gh_state["repo"]["delete_branch_on_merge"] is True
    setup = load(repo, "config/github/ruleset-policy.json")["setup_state"]
    assert setup["application_status"] == "applied_verified" and setup["enforcement_verified"] is True
    assert json.loads(run(repo, "rules", "verify").stdout)["enforced_and_matching"] is True
    # someone weakens the ruleset in GitHub
    gh_state = state(gh)
    rid = list(gh_state["rulesets"])[0]
    gh_state["rulesets"][rid]["rules"] = [r for r in gh_state["rulesets"][rid]["rules"] if r["type"] != "deletion"]
    put(gh, gh_state)
    out = run(repo, "rules", "verify", ok=False)
    assert out.returncode == 1 and "missing rule deletion" in out.stdout
    assert json.loads(run(repo, "audit", "verify").stdout)["intact"] is True


def test_github_without_admin_is_recorded_not_claimed(tmp_path: Path, gh: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    put(gh, {"repo": {"private": True, "permissions": {"admin": False}}})
    run(repo, "vcs", "select", "github")
    approve_change(repo, "rules", "propose", "--profile", "solo")
    setup = load(repo, "config/github/ruleset-policy.json")["setup_state"]
    assert setup["application_status"] == "insufficient_permission" and setup["enforcement_verified"] is False
    assert "rulesets" not in state(gh)


@pytest.mark.parametrize("host,remote", [("bitbucket", "git@bitbucket.org:acme/app.git"),
                                         ("azure_devops", "https://dev.azure.com/acme/p/_git/app"),
                                         ("gitea", "https://codeberg.org/acme/app.git"),
                                         ("local", None)])
def test_guided_hosts_get_exact_steps(tmp_path: Path, host: str, remote: str | None) -> None:
    repo = new_repo(tmp_path / "g", remote or "https://example.invalid/x/y.git")
    if remote is None:
        git(repo, "remote", "remove", "origin")
    run(repo, "init", "--full", "--repository", "acme/app")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "anpos")
    assert json.loads(run(repo, "vcs", "detect").stdout)["suggested_host"] == host
    run(repo, "vcs", "select", host)
    out = approve_change(repo, "rules", "propose", "--profile", "solo")
    assert out
    setup = load(repo, "config/github/ruleset-policy.json")["setup_state"]
    assert setup["application_status"] == "manual_guide_generated" and setup["enforcement_verified"] is False
    guide = (repo / setup["guide"]).read_text()
    assert "not enforced" in guide and "1." in guide
    assert "rules are manual_guide_generated" in run(repo, "rules", "verify", ok=False).stdout
    run(repo, "rules", "confirm-manual", "--note", "set in UI")
    assert load(repo, "config/github/ruleset-policy.json")["setup_state"]["application_status"] == "user_reported_applied"


def test_gitlab_rules_auto(tmp_path: Path, glab: Path) -> None:
    repo = full_repo(tmp_path, "https://gitlab.com/acme/app.git")
    run(repo, "vcs", "select", "gitlab")
    approve_change(repo, "rules", "propose", "--profile", "team")
    s = state(glab)
    branch = load(repo, "config/integrations/vcs-host.json")["default_branch"]
    assert s["protected"][branch]["allow_force_push"] is False
    assert s["project"]["merge_method"] == "ff" and s["project"]["squash_option"] == "default_on"
    setup = load(repo, "config/github/ruleset-policy.json")["setup_state"]
    assert setup["application_status"] == "applied_verified"
    assert any("Premium" in str(v) for v in [setup["verification"]])


def test_quality_per_host(tmp_path: Path, gh: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    run(repo, "vcs", "select", "github")
    caps = json.loads(run(repo, "quality", "detect").stdout)["capabilities"]
    assert caps == {"codeql": True, "dependency-review": True, "scorecard": True}
    files = json.loads(run(repo, "quality", "apply").stdout)["files"]
    assert ".github/workflows/codeql-actions.yml" in files
    run(repo, "quality", "record-tool", "--category", "linter", "--tool", "ruff", "--command", "ruff check .")
    assert load(repo, "config/quality/quality-policy.json")["setup_state"]["stack_tools"][0]["tool"] == "ruff"
    assert "0 error(s)" in run(repo, "validate").stdout

    other = full_repo(tmp_path / "gl", "https://gitlab.com/acme/app.git")
    run(other, "vcs", "select", "gitlab")
    out = json.loads(run(other, "quality", "apply", "--with-sast").stdout)
    ci = (other / ".gitlab-ci.yml").read_text()
    assert out["files"] == [".gitlab-ci.yml"] and "SAST" in ci and "validate_ai_native_repo.py" in ci
    run(other, "quality", "local-hooks")
    assert (other / ".git/hooks/pre-push").exists()


# ---------------------------------------------------------------- assurance, ADR, assets, audit, memory

def test_assurance_rules(tmp_path: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    assert "--reason" in run(repo, "assurance", "classify", "REQ-83", "not_applicable", ok=False).stderr
    run(repo, "assurance", "classify", "REQ-83", "not_applicable", "--reason", "no AI in the product")
    assert "evidence first" in run(repo, "assurance", "set-state", "REQ-91", "passed", ok=False).stderr
    adr = run(repo, "adr", "--title", "Monolith", "--context", "small team", "--decision", "one service",
              "--alternative", "microservices").stdout.strip()
    assert adr == "ADR-0001"
    run(repo, "assurance", "evidence", "REQ-91", "--ref", adr)
    run(repo, "assurance", "finding", "REQ-91", "ADR missing for auth")
    assert "blocking findings" in run(repo, "assurance", "set-state", "REQ-91", "passed", ok=False).stderr
    run(repo, "assurance", "finding", "REQ-91", "ADR missing for auth", "--clear")
    run(repo, "assurance", "set-state", "REQ-91", "passed")
    row = [r for r in json.loads(run(repo, "assurance", "status").stdout) if r["id"] == "REQ-91"][0]
    assert row["state"] == "passed" and row["evidence"] == 1
    adr2 = run(repo, "adr", "--title", "Split auth", "--context", "growth", "--decision", "auth service",
               "--supersedes", "ADR-0001").stdout.strip()
    decisions = load(repo, "config/architecture/decision-records.json")["decisions"]
    assert decisions[0]["status"] == "superseded" and decisions[0]["superseded_by"] == adr2
    assert "secret" in run(repo, "ai-asset", "--type", "model", "--name", "gpt", "--config-hash", "sk-abc123",
                           ok=False).stderr
    assert run(repo, "ai-asset", "--type", "prompt", "--name", "support-bot system prompt",
               "--config-hash", "sha256:1234").stdout.strip() == "AI-ASSET-0001"
    assert "0 error(s)" in run(repo, "validate").stdout


def test_audit_chain_detects_tampering(tmp_path: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    for i in range(3):
        run(repo, "audit", "append", "--action", "test", "--target", f"t{i}", "--result", "ok")
    assert json.loads(run(repo, "audit", "verify").stdout)["intact"] is True
    journal = load(repo, "config/audit/audit-journal.json")
    journal["entries"][1]["result"] = "edited"
    save(repo, "config/audit/audit-journal.json", journal)
    out = json.loads(run(repo, "audit", "verify", ok=False).stdout)
    assert out["intact"] is False and "AUDIT-000002" in out["problems"][0]


def test_memory_provenance(tmp_path: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    assert "trusted authority" in run(repo, "memory", "add", "--target", "research_fact", "--summary", "x",
                                      "--source-type", "external", "--trust", "trusted_authority", ok=False).stderr
    mid = run(repo, "memory", "add", "--target", "preference", "--summary", "use pnpm", "--source-type", "user").stdout.strip()
    entry = load(repo, "config/ai/memory-provenance.json")["entries"][0]
    assert mid == "MEM-000001" and entry["trust_level"] == "trusted_authority"
    run(repo, "memory", "add", "--target", "preference", "--summary", "use npm", "--source-type", "user",
        "--supersedes", mid)
    assert load(repo, "config/ai/memory-provenance.json")["entries"][0]["validation_status"] == "superseded"


# ---------------------------------------------------------------- improvement and host approvals

def test_watch_and_scout(tmp_path: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    assert "stack is approved" in run(repo, "watch", "enable", ok=False).stderr
    (repo / "package.json").write_text("{}")
    status = json.loads(run(repo, "watch", "status").stdout)
    assert status["dependency_manifests"] == ["package.json"] and status["due"] is True
    assert "not enabled" in run(repo, "scout", "suggest", "--title", "x", "--rationale", "y", ok=False).stderr
    approve_change(repo, "scout", "enable")
    assert run(repo, "scout", "suggest", "--title", "Offline mode", "--rationale", "competitors have it").stdout.strip() == "SUG-0001"


def test_host_comment_approvals(tmp_path: Path, gh: Path) -> None:
    repo = full_repo(tmp_path, "https://github.com/acme/app.git")
    run(repo, "evidence", "add", "--claim", "c", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "low", "--summary", "s")
    write_plan(repo)
    run(repo, "vcs", "select", "github", "--approval-channel", "host_comment")
    out = json.loads(run(repo, "consent", "propose", "--type", "plan_approval").stdout)
    assert out["approval_issue"].startswith("https://github.com/acme/app/issues/")
    number = out["approval_issue"].rsplit("/", 1)[-1]
    # a chat phrase is not enough when the channel is the host
    hook(CAPTURE, {"prompt": out["approve_phrase"]}, repo)
    assert "no approve/reject comment" in run(repo, "consent", "confirm", ok=False).stderr
    s = state(gh)
    s["comments"] = {number: [{"body": out["approve_phrase"], "created_at": "2099-01-01T00:00:00Z",
                               "user": {"login": "outsider"}, "html_url": "c0"}]}
    put(gh, s)
    assert "no approve/reject comment" in run(repo, "consent", "confirm", ok=False).stderr  # outside window
    req = load(repo, "config/consent/consent-requests.json")["requests"][-1]
    s["comments"][number] = [
        {"body": out["approve_phrase"], "created_at": req["requested_at"], "user": {"login": "outsider"}, "html_url": "c1"},
        {"body": f"lgtm {out['approve_phrase']}", "created_at": req["requested_at"], "user": {"login": "owner"},
         "html_url": "https://github.com/acme/app/issues/1#c2"}]
    s["perms"] = {"outsider": "write", "owner": "admin"}
    put(gh, s)
    run(repo, "consent", "confirm")
    req = load(repo, "config/consent/consent-requests.json")["requests"][-1]
    assert req["status"] == "approved" and req["decision_by"] == "github:owner"
    assert load(repo, "config/ai/execution-plan.json")["plan_status"] == "approved"
    assert "0 error(s)" in run(repo, "validate").stdout
