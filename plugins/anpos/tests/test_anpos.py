"""End-to-end tests for the ANPOS planner plugin scripts and hooks.

Run:  python3 -m pytest plugins/anpos/tests -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
CLI = PLUGIN / "scripts" / "anpos.py"
CAPTURE = PLUGIN / "hooks" / "approval_capture.py"
GUARD = PLUGIN / "hooks" / "guard.py"


def run(root: Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, str(CLI), "--root", str(root), *args], capture_output=True, text=True)
    if ok:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def hook(script: Path, payload: dict, root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    return subprocess.run([sys.executable, str(script)], input=json.dumps(payload), capture_output=True, text=True, env=env)


def load(root: Path, rel: str) -> dict:
    return json.loads((root / rel).read_text())


def save(root: Path, rel: str, doc: dict) -> None:
    (root / rel).write_text(json.dumps(doc, indent=2) + "\n")


def write_plan(root: Path) -> None:
    opts = load(root, "config/ai/options-bank.json")
    opts["options"] = [{"id": "OPT-AUTH", "name": "Email login", "category": "auth", "description": "Email + password",
                        "status": "selected", "requirement_source": ["REQ-001"], "dependencies": [], "conflicts": [], "rationale": "Simplest secure option"}]
    save(root, "config/ai/options-bank.json", opts)
    mods = load(root, "config/ai/modules-bank.json")
    mods["modules"] = [
        {"id": "MOD-AUTH", "name": "Auth", "objective": "Sign in", "scope": ["login"], "boundaries": [],
         "dependencies": [], "option_ids": ["OPT-AUTH"], "acceptance_criteria": ["user can log in"], "status": "not_started"},
        {"id": "MOD-TASKS", "name": "Tasks", "objective": "Manage tasks", "scope": ["crud"], "boundaries": [],
         "dependencies": ["MOD-AUTH"], "option_ids": [], "acceptance_criteria": ["CRUD works"], "status": "not_started"},
    ]
    save(root, "config/ai/modules-bank.json", mods)
    plan = load(root, "config/ai/execution-plan.json")
    plan["phases"] = [{"id": "PHASE-1", "name": "MVP", "objective": "Usable MVP", "module_ids": ["MOD-AUTH", "MOD-TASKS"],
                       "dependencies": [], "status": "not_started", "exit_criteria": ["demo"]}]
    base = {"phase_id": "PHASE-1", "status": "not_started", "required_checks": ["tests"], "risk_classification": "normal",
            "allowed_paths": ["src/**"], "priority": 100}
    plan["work_units"] = [
        dict(base, id="WU-AUTH-1", module_id="MOD-AUTH", requirement_ids=["REQ-001"], title="Login API",
             objective="login endpoint", dependencies=[], acceptance_criteria=["returns token"]),
        dict(base, id="WU-TASK-1", module_id="MOD-TASKS", requirement_ids=["REQ-002"], title="Task CRUD",
             objective="crud", dependencies=["WU-AUTH-1"], acceptance_criteria=["crud tested"]),
    ]
    save(root, "config/ai/execution-plan.json", plan)
    trace = load(root, "config/traceability/requirements-traceability.json")
    trace["links"] = [
        {"requirement_id": "REQ-001", "source": "EVID-000001", "option_ids": ["OPT-AUTH"], "module_ids": ["MOD-AUTH"],
         "work_unit_ids": ["WU-AUTH-1"], "status": "planned", "verification_status": "unverified"},
        {"requirement_id": "REQ-002", "source": "user intake", "option_ids": [], "module_ids": ["MOD-TASKS"],
         "work_unit_ids": ["WU-TASK-1"], "status": "planned", "verification_status": "unverified"},
    ]
    save(root, "config/traceability/requirements-traceability.json", trace)
    write_competitive(root)


COMPETITIVE_MD = """# Competitive Analysis

| Capability | Competitor A | Competitor B | This product |
|---|---|---|---|
| Login | ✅ | ⚠️ paid | ✅ REQ-001 (EVID-000001) |
"""


def write_competitive(root: Path) -> None:
    if not load(root, "config/research/evidence-registry.json").get("entries"):
        run(root, "evidence", "add", "--claim", "Competitors offer login", "--source-type", "secondary", "--source-ref", "https://example.com",
            "--class", "supported_inference", "--confidence", "low", "--summary", "test evidence")
    (root / "docs/ai").mkdir(parents=True, exist_ok=True)
    (root / "docs/ai/COMPETITIVE-ANALYSIS.md").write_text(COMPETITIVE_MD)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("demo\n")
    run(tmp_path, "init", "--project-name", "Demo")
    run(tmp_path, "evidence", "add", "--claim", "Users need login", "--source-type", "user", "--source-ref", "intake",
        "--class", "supported_inference", "--confidence", "medium", "--summary", "From intake")
    return tmp_path


def approve(root: Path, kind: str = "PLAN", decision: str = "APPROVE") -> dict:
    out = json.loads(run(root, "consent", "propose", "--type", "plan_approval").stdout)
    res = hook(CAPTURE, {"prompt": f"ok. {decision} {kind} {out['approval_code']}", "session_id": "s1", "cwd": str(root)}, root)
    assert res.returncode == 0 and "captured" in res.stdout
    return out


def test_detect_states(tmp_path: Path) -> None:
    (tmp_path / "config/protocol").mkdir(parents=True)
    (tmp_path / "config/protocol/instance.json").write_text(json.dumps({"instance_status": "template_source"}))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://github.com/Vertex-Systems-Network/ai-native-project-operating-system.git"], cwd=tmp_path, check=True)
    assert json.loads(run(tmp_path, "detect").stdout)["state"] == "canonical_source"
    assert run(tmp_path, "init", ok=False).returncode == 2
    subprocess.run(["git", "remote", "set-url", "origin", "git@github.com:acme/app.git"], cwd=tmp_path, check=True)
    assert json.loads(run(tmp_path, "detect").stdout)["state"] == "uninitialized_child"
    assert run(tmp_path, "init", ok=False).returncode == 3


def test_init_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "PROJECT-IDEA.md").write_text("mine\n")
    run(tmp_path, "init")
    assert (tmp_path / "PROJECT-IDEA.md").read_text() == "mine\n"
    assert json.loads(run(tmp_path, "detect").stdout)["state"] == "planner_lite"


def test_full_flow_and_gates(repo: Path) -> None:
    assert run(repo, "stage", "system_design", ok=False).returncode == 1
    write_plan(repo)
    assert "0 error(s)" in run(repo, "validate").stdout
    out = json.loads(run(repo, "consent", "propose", "--type", "plan_approval").stdout)
    assert load(repo, "config/ai/execution-plan.json")["plan_status"] == "proposed"
    # confirming without the user's phrase fails
    assert "no user decision captured" in run(repo, "consent", "confirm", ok=False).stderr
    hook(CAPTURE, {"prompt": f"APPROVE PLAN {out['approval_code']}", "session_id": "s1"}, repo)
    run(repo, "consent", "confirm")
    plan = load(repo, "config/ai/execution-plan.json")
    assert plan["plan_status"] == "approved" and plan["plan_approval_ref"] == out["consent_id"]
    assert load(repo, "config/ai/project-state.json")["lifecycle_stage"] == "plan_approved"
    assert "0 error(s)" in run(repo, "validate").stdout
    # token is single use
    assert run(repo, "consent", "confirm", ok=False).returncode == 1
    # execution: dependency order enforced
    assert "WU-AUTH-1" in run(repo, "next").stdout and "WU-TASK-1" not in run(repo, "next").stdout
    assert run(repo, "wu", "WU-TASK-1", "in_progress", ok=False).returncode == 1
    assert run(repo, "wu", "WU-AUTH-1", "complete", ok=False).returncode == 1  # needs evidence
    run(repo, "wu", "WU-AUTH-1", "complete", "--evidence", "pytest 12 passed")
    assert "WU-TASK-1" in run(repo, "next").stdout
    # progress changes do not invalidate the approval
    assert "plan content changed" not in run(repo, "validate").stdout
    # stage 9 gate
    run(repo, "stage", "system_design")
    assert run(repo, "stage", "architecture", ok=False).returncode == 1
    assert "SYSTEM-DESIGN.md" in run(repo, "consent", "propose", "--type", "technology_stack", "--change", "x",
                                     ok=False).stderr
    (repo / "docs/ai/SYSTEM-DESIGN.md").write_text("# System design\n\n" + "Boundaries, actors and NFRs. " * 20)
    (repo / "docs/ai/ARCHITECTURE.md").write_text("# Architecture\n\n" + "Modules, contracts, CI/CD. " * 20)
    stack = json.loads(run(repo, "consent", "propose", "--type", "technology_stack", "--change", "Frontend: Next.js",
                           "--change", "DB: Postgres").stdout)
    hook(CAPTURE, {"prompt": f"APPROVE STACK {stack['approval_code']}", "session_id": "s1"}, repo)
    run(repo, "consent", "confirm")
    assert load(repo, "config/ai/project-state.json")["lifecycle_stage"] == "architecture"
    run(repo, "stage", "data_flow")
    assert "0 error(s)" in run(repo, "validate").stdout


def test_plan_change_after_proposal_requires_new_proposal(repo: Path) -> None:
    write_plan(repo)
    out = approve(repo)
    plan = load(repo, "config/ai/execution-plan.json")
    plan["work_units"][0]["title"] = "Something else"
    save(repo, "config/ai/execution-plan.json", plan)
    assert "plan changed after it was proposed" in run(repo, "consent", "confirm", ok=False).stderr
    assert out["approve_phrase"].startswith("APPROVE PLAN ")


def test_material_change_after_approval_is_flagged(repo: Path) -> None:
    write_plan(repo)
    approve(repo)
    run(repo, "consent", "confirm")
    with open(repo / "docs/ai/PRE-PLAN.md", "a") as handle:
        handle.write("\n")  # trailing whitespace only: not material
    assert "plan content changed" not in run(repo, "validate").stdout
    text = (repo / "docs/ai/PRE-PLAN.md").read_text().replace("## 2. Scope", "## 2. Scope\n\n- new scope item")
    (repo / "docs/ai/PRE-PLAN.md").write_text(text)
    assert "plan content changed" in run(repo, "validate").stdout
    assert run(repo, "stage", "planning", ok=False).returncode == 1
    run(repo, "stage", "planning", "--revise")
    plan = load(repo, "config/ai/execution-plan.json")
    assert plan["plan_status"] == "draft" and plan["plan_approval_ref"] is None


def test_reject_returns_to_planning(repo: Path) -> None:
    write_plan(repo)
    approve(repo, decision="REJECT")
    run(repo, "consent", "confirm")
    assert load(repo, "config/ai/execution-plan.json")["plan_status"] == "draft"
    assert load(repo, "config/ai/project-state.json")["lifecycle_stage"] == "planning"


def test_tampered_consent_detected(repo: Path) -> None:
    write_plan(repo)
    approve(repo)
    doc = load(repo, "config/consent/consent-requests.json")
    doc["requests"][-1]["summary"] = "edited"
    save(repo, "config/consent/consent-requests.json", doc)
    assert "hash mismatch" in run(repo, "consent", "confirm", ok=False).stderr
    assert "tampered" in run(repo, "validate", ok=False).stdout


def test_validation_catches_broken_refs_and_cycles(repo: Path) -> None:
    write_plan(repo)
    plan = load(repo, "config/ai/execution-plan.json")
    plan["work_units"][0]["dependencies"] = ["WU-TASK-1"]
    plan["work_units"][1]["requirement_ids"] = ["REQ-404"]
    save(repo, "config/ai/execution-plan.json", plan)
    out = run(repo, "validate", ok=False).stdout
    assert "dependency cycle" in out and "REQ-404" in out
    assert run(repo, "consent", "propose", "--type", "plan_approval", ok=False).returncode == 1


def test_approval_captured_when_session_opened_in_parent_folder(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    write_plan(repo)
    out = json.loads(run(repo, "consent", "propose", "--type", "plan_approval").stdout)
    session_dir = repo.parent  # the Claude Code session was opened one level above the project
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(session_dir))
    res = subprocess.run([sys.executable, str(CAPTURE)], input=json.dumps(
        {"prompt": out["approve_phrase"], "session_id": "s9", "cwd": str(session_dir)}), capture_output=True, text=True, env=env)
    assert "captured" in res.stdout, res.stdout
    run(repo, "consent", "confirm")
    assert load(repo, "config/ai/execution-plan.json")["plan_status"] == "approved"
    assert "out of date" not in run(repo, "validate").stdout  # progress refreshed on approval
    assert not any(Path(os.environ["ANPOS_POINTER_HOME"]).iterdir())  # pointer removed after the decision
    # an unknown code typed in an unrelated folder changes nothing and stays silent
    elsewhere = tmp_path_factory.mktemp("not-a-project")
    res = subprocess.run([sys.executable, str(CAPTURE)], input=json.dumps({"prompt": "APPROVE PLAN DEADBEEF", "cwd": str(elsewhere)}),
                         capture_output=True, text=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(elsewhere)))
    assert res.stdout == "" and not (elsewhere / ".anpos").exists()


def test_plan_needs_market_comparison(repo: Path) -> None:
    write_plan(repo)
    comp = repo / "docs/ai/COMPETITIVE-ANALYSIS.md"
    comp.unlink()
    err = run(repo, "consent", "propose", "--type", "plan_approval", ok=False).stderr
    assert "competitive-superiority" in err and "COMPETITIVE-ANALYSIS.md is missing" in err
    comp.write_text("# Competitors\n\n| Capability | Us |\n|---|---|\n| Login | ✅ |\n")
    err = run(repo, "consent", "propose", "--type", "plan_approval", ok=False).stderr
    assert "at least two competitors" in err and "must cite" in err
    write_competitive(repo)
    assert json.loads(run(repo, "consent", "propose", "--type", "plan_approval").stdout)["approve_phrase"].startswith("APPROVE PLAN")
    comp.unlink()
    out = json.loads(run(repo, "consent", "propose", "--type", "plan_approval", "--no-market-comparison", "internal admin tool").stdout)
    record = [r for r in load(repo, "config/consent/consent-requests.json")["requests"] if r["id"] == out["consent_id"]][0]
    assert "market_comparison:not_applicable:internal admin tool" in record["proposed_changes"]


def test_guard_blocks_self_approval(repo: Path) -> None:
    plan_path = str(repo / "config/ai/execution-plan.json")
    cases = [
        {"tool_name": "Write", "tool_input": {"file_path": str(repo / ".anpos/approvals/ABCDEF12.json"), "content": "{}"}},
        {"tool_name": "Edit", "tool_input": {"file_path": str(repo / "config/consent/consent-requests.json"),
                                             "old_string": "a", "new_string": "b"}},
        {"tool_name": "Edit", "tool_input": {"file_path": plan_path, "old_string": '"plan_status": "proposed"',
                                             "new_string": '"plan_status": "approved"'}},
        {"tool_name": "Bash", "tool_input": {"command": "echo '{}' > .anpos/approvals/ABCDEF12.json"}},
        {"tool_name": "Bash", "tool_input": {"command": "sed -i s/pending/approved/ config/consent/consent-requests.json"}},
    ]
    for payload in cases:
        assert hook(GUARD, payload, repo).returncode == 2, payload
    allowed = [
        {"tool_name": "Edit", "tool_input": {"file_path": plan_path, "old_string": '"title": "a"', "new_string": '"title": "b"'}},
        {"tool_name": "Bash", "tool_input": {"command": "python3 /x/scripts/anpos.py consent confirm"}},
        {"tool_name": "Read", "tool_input": {"file_path": str(repo / "config/consent/consent-requests.json")}},
    ]
    for payload in allowed:
        assert hook(GUARD, payload, repo).returncode == 0, payload


def test_capture_ignores_non_anpos_dirs_and_first_token_wins(tmp_path: Path, repo: Path) -> None:
    assert hook(CAPTURE, {"prompt": "APPROVE PLAN ABCDEF12"}, tmp_path / "nothing").stdout == ""
    hook(CAPTURE, {"prompt": "approve plan abcdef12"}, repo)
    hook(CAPTURE, {"prompt": "REJECT PLAN ABCDEF12"}, repo)
    assert load(repo, ".anpos/approvals/ABCDEF12.json")["decision"] == "approve"


def test_status_brief_silent_outside_anpos(tmp_path: Path, repo: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    assert run(other, "status", "--brief").stdout == ""
    assert "ANPOS project detected" in run(repo, "status", "--brief").stdout


def test_upstream_compatible_request_hash() -> None:
    """Records created here must verify with upstream scripts/consent_guard.py hashing."""
    sys.path.insert(0, str(CLI.parent))
    import anpos  # type: ignore

    record = {"id": "CONSENT-000001", "type": "plan_approval", "title": "t", "summary": "s", "proposed_changes": ["x"],
              "affected_modules": [], "risk": "low", "owner_identity": None, "requested_at": "2026-01-01T00:00:00Z",
              "expires_at": "2026-01-02T00:00:00Z", "nonce": "n" * 32}
    expected_payload = json.dumps({k: record.get(k) for k in (
        "id", "type", "title", "summary", "proposed_changes", "affected_modules", "risk", "owner_identity",
        "requested_at", "expires_at", "nonce")}, sort_keys=True, separators=(",", ":"))
    assert anpos.canonical_payload(record) == expected_payload


def test_guard_allows_reading_and_committing_records(repo: Path) -> None:
    allowed = [
        "git add config/consent/consent-requests.json .anpos/approvals",
        "git diff config/consent/consent-requests.json",
        "cat .anpos/approvals/ABCDEF12.json",
        "git commit -m 'plan approved' 2>&1 && git status",
        "ls .anpos/approvals >/dev/null",
        'sh "/p/scripts/anpos.sh" consent confirm',
        "cat > docs/notes.md <<'EOF'\nTokens live in .anpos/approvals/ and consent-requests.json\nEOF",
        'git add -A && git commit -m "Record approvals\n\nEvidence in .anpos/approvals and consent-requests.json"',
        "git commit -m \"$(cat <<'EOF'\nApprovals: .anpos/approvals/A.json\nEOF\n)\"",
    ]
    for command in allowed:
        assert hook(GUARD, {"tool_name": "Bash", "tool_input": {"command": command}}, repo).returncode == 0, command
    blocked = [
        "cat x | tee .anpos/approvals/ABCDEF12.json",
        "sh /p/scripts/anpos.sh status; echo '{}' > .anpos/approvals/ABCDEF12.json",
        "cp /tmp/fake.json config/consent/consent-requests.json",
        "cd .anpos/approvals && echo x > A.json",
        "python3 -c \"open('config/consent/consent-requests.json','w')\"",
        "cat > .anpos/approvals/ABCDEF12.json <<'EOF'\n{\"decision\": \"approve\"}\nEOF",
        'echo "{}" > ".anpos/approvals/ABCDEF12.json"',
        'git status; mv /tmp/x "config/consent/consent-requests.json"',
    ]
    for command in blocked:
        assert hook(GUARD, {"tool_name": "Bash", "tool_input": {"command": command}}, repo).returncode == 2, command


def test_export_formats(repo: Path) -> None:
    write_plan(repo)
    md = run(repo, "export").stdout
    assert "NOT APPROVED" in md and "WU-AUTH-1" in md and "- [ ] returns token" in md
    approve(repo)
    run(repo, "consent", "confirm")
    csv_text = run(repo, "export", "--format", "csv").stdout
    assert csv_text.splitlines()[0].startswith("ID,Title,Description") and len(csv_text.splitlines()) == 3
    issues = json.loads(run(repo, "export", "--format", "issues").stdout)
    assert issues[0]["title"] == "[WU-AUTH-1] Login API" and "anpos" in issues[0]["labels"]
    assert "approved via CONSENT-" in issues[0]["body"]
    run(repo, "export", "--output", "docs/ai/PLAN-EXPORT.md")
    assert (repo / "docs/ai/PLAN-EXPORT.md").exists()


def test_launcher_finds_python_and_passes_exit_codes(repo: Path) -> None:
    launcher = PLUGIN / "scripts" / "anpos.sh"
    ok = subprocess.run(["sh", str(launcher), "--root", str(repo), "status"], capture_output=True, text=True)
    assert ok.returncode == 0 and "lifecycle_stage" in ok.stdout
    bad = subprocess.run(["sh", str(launcher), "--root", str(repo), "stage", "plan_approved"], capture_output=True, text=True)
    assert bad.returncode == 1
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(repo / ".anpos" / "approvals" / "X.json")}}
    guard = subprocess.run(["sh", str(PLUGIN / "scripts" / "pyrun.sh"), "hooks/guard.py"], capture_output=True,
                           text=True, input=json.dumps(payload))
    assert guard.returncode == 2
