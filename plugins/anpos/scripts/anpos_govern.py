"""Governance for the anpos plugin: version-control host, Code Quality, branch rules, assurance (Req 83-96),
tamper-evident audit journal, memory provenance, continuous improvement and host-authenticated approvals.

Branch rules are provider-neutral (config/github/ruleset-policy.json is the desired policy). The user picks the
host and a profile; where the plugin can act (GitHub via `gh`, GitLab via `glab`, with admin/maintainer rights)
it applies the rules after an `APPROVE CHANGE` consent and re-reads them to verify. Everywhere else it writes an
exact step-by-step guide for that host and records the setup as manual/pending — never as enforced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anpos as core
from anpos import AnposError, emit, find_root, iso, now, parse_time, read_json, write_json

F_VCS = "config/integrations/vcs-host.json"
F_RULES = "config/github/ruleset-policy.json"
F_QUALITY = "config/quality/quality-policy.json"
F_ASSURANCE = "config/assurance/assurance-state.json"
F_AUDIT = "config/audit/audit-journal.json"
F_MEMORY = "config/ai/memory-provenance.json"
F_ADR = "config/architecture/decision-records.json"
F_ASSETS = "config/ai/asset-registry.json"
F_WATCH = "config/maintenance/technology-watch.json"
F_SCOUT = "config/maintenance/innovation-scout.json"
GUIDES = "docs/anpos/governance"
RULESET_NAME = "ANPOS default branch protection"

HOSTS = {
    "github": {"name": "GitHub", "cli": "gh", "auto": True},
    "gitlab": {"name": "GitLab", "cli": "glab", "auto": True},
    "bitbucket": {"name": "Bitbucket", "cli": None, "auto": False},
    "azure_devops": {"name": "Azure DevOps Repos", "cli": "az", "auto": False},
    "gitea": {"name": "Gitea / Forgejo / Codeberg", "cli": "tea", "auto": False},
    "local": {"name": "Local git only (no hosting)", "cli": None, "auto": False},
    "other": {"name": "Other / self-hosted", "cli": None, "auto": False},
}


def require_full(root: Path) -> None:
    if not core.is_full_child(root):
        raise AnposError("this needs a full ANPOS child (run `init --full` first)")


def remote_url(root: Path) -> str | None:
    out = core.git(root, "config", "--get", "remote.origin.url", check=False)
    return out or None


def guess_host(url: str | None) -> str:
    if not url:
        return "local"
    u = url.lower()
    if "github.com" in u:
        return "github"
    if "gitlab" in u:
        return "gitlab"
    if "bitbucket" in u:
        return "bitbucket"
    if "dev.azure.com" in u or "visualstudio.com" in u:
        return "azure_devops"
    if any(k in u for k in ("gitea", "forgejo", "codeberg")):
        return "gitea"
    return "other"


def cli_ready(name: str | None) -> tuple[bool, str]:
    if not name:
        return False, "no supported CLI"
    if not shutil.which(name):
        return False, f"`{name}` is not installed"
    if name in ("gh", "glab"):
        proc = subprocess.run([name, "auth", "status"], capture_output=True, text=True)
        return (proc.returncode == 0, f"{name} logged in" if proc.returncode == 0 else f"run `{name} auth login`")
    return True, f"{name} found"


def vcs_load(root: Path) -> dict[str, Any]:
    if (root / F_VCS).exists():
        return read_json(root, F_VCS)
    return {"schema_version": 1, "host": None, "repository": None, "default_branch": None,
            "approval_channel": "chat", "selected_at": None, "managed_by": "anpos plugin"}


def run_cli(*cmd: str, stdin: str | None = None, ok_codes: tuple[int, ...] = (0,)) -> tuple[int, str]:
    proc = subprocess.run(list(cmd), input=stdin, capture_output=True, text=True)
    return proc.returncode, (proc.stdout if proc.returncode in ok_codes else (proc.stderr or proc.stdout)).strip()


def gh_json(*args: str, stdin: str | None = None) -> Any:
    code, out = run_cli("gh", "api", *args, stdin=stdin)
    if code != 0:
        raise AnposError(f"gh api {' '.join(args[:3])} failed: {out[-400:]}")
    return json.loads(out) if out else {}


def glab_json(*args: str) -> Any:
    code, out = run_cli("glab", "api", *args)
    if code != 0:
        raise AnposError(f"glab api {' '.join(args[:3])} failed: {out[-400:]}")
    return json.loads(out) if out else {}


def project_path(root: Path) -> str:
    slug = core.git_remote_slug(root)
    url = remote_url(root) or ""
    m = re.search(r"(?:gitlab[^/:]*)[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else (slug or "")


# ---------------------------------------------------------------- audit journal (REQ-95)

def _entry_hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit_log(root: Path, action: str, target: str, result: str, consent_ref: str | None = None,
              actor: str = "anpos-plugin", role: str = "host_runtime", risk: str = "normal",
              tool: str | None = None, result_ref: str | None = None) -> str | None:
    """Append a hash-chained audit entry (full ANPOS children only). Never stores secrets or prompts."""
    if not (root / F_AUDIT).exists() or not core.is_full_child(root):
        return None
    journal = read_json(root, F_AUDIT)
    entries = journal.setdefault("entries", [])
    previous = entries[-1]["entry_hash"] if entries else None
    entry = {
        "id": f"AUDIT-{len(entries) + 1:06d}", "actor_identity_ref": actor, "role": role, "action": action,
        "target": target, "tool_or_provider": tool, "base_ref": core.git(root, "rev-parse", "HEAD", check=False) or None,
        "result_ref": result_ref, "consent_ref": consent_ref, "sanitized_input_hash": None,
        "sanitized_output_or_artifact_hash": None, "occurred_at": iso(now()), "result": result,
        "risk_class": risk, "previous_entry_hash": previous,
    }
    entry["entry_hash"] = _entry_hash(entry)
    entries.append(entry)
    integrity = journal.setdefault("integrity", {"mode": "sha256_hash_chain"})
    integrity["genesis_hash"] = integrity.get("genesis_hash") or entry["entry_hash"]
    integrity["latest_entry_hash"] = entry["entry_hash"]
    write_json(root, F_AUDIT, journal)
    return entry["id"]


def audit_verify(root: Path) -> list[str]:
    journal = read_json(root, F_AUDIT)
    problems, previous = [], None
    for e in journal.get("entries", []):
        if e.get("previous_entry_hash") != previous:
            problems.append(f"{e.get('id')}: chain broken (previous hash mismatch)")
        if _entry_hash(e) != e.get("entry_hash"):
            problems.append(f"{e.get('id')}: entry was modified after it was written")
        previous = e.get("entry_hash")
    integ = journal.get("integrity") or {}
    if journal.get("entries") and integ.get("latest_entry_hash") != previous:
        problems.append("integrity.latest_entry_hash does not match the last entry")
    return problems


def cmd_audit(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    if args.audit_cmd == "append":
        aid = audit_log(root, args.action, args.target, args.result, args.consent, actor=args.actor or "claude-code",
                        role=args.role, risk=args.risk, tool=args.tool, result_ref=args.result_ref)
        print(aid)
        return 0
    if args.audit_cmd == "verify":
        problems = audit_verify(root)
        journal = read_json(root, F_AUDIT)
        if not problems:
            journal.setdefault("integrity", {})["last_verified_at"] = iso(now())
            write_json(root, F_AUDIT, journal)
        emit({"entries": len(journal.get("entries", [])), "intact": not problems, "problems": problems}, True)
        return 0 if not problems else 1
    journal = read_json(root, F_AUDIT)
    emit([{k: e.get(k) for k in ("id", "occurred_at", "action", "target", "result", "consent_ref")}
          for e in journal.get("entries", [])[-args.limit:]], True)
    return 0


# ---------------------------------------------------------------- VCS host

def cmd_vcs(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = vcs_load(root)
    if args.vcs_cmd == "detect":
        url = remote_url(root)
        guess = guess_host(url)
        rows = []
        for hid, spec in HOSTS.items():
            ready, detail = cli_ready(spec["cli"]) if spec["auto"] else (False, "guided setup")
            rows.append({"id": hid, "name": spec["name"], "automation": "auto" if spec["auto"] and ready else "guide",
                         "detail": detail, "suggested": hid == guess})
        emit({"remote": url, "suggested_host": guess, "hosts": rows, "current": state.get("host")}, True)
        return 0
    if args.vcs_cmd == "select":
        if args.host not in HOSTS:
            raise AnposError(f"unknown host {args.host}; choose one of {', '.join(HOSTS)}")
        branch = args.default_branch or core.git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD",
                                                 check=False).removeprefix("origin/") or \
            core.git(root, "branch", "--show-current", check=False) or "main"
        state.update({"host": args.host, "repository": project_path(root) or None, "default_branch": branch,
                      "selected_at": iso(now())})
        if args.approval_channel:
            if args.approval_channel == "host_comment" and args.host not in ("github", "gitlab"):
                raise AnposError("host_comment approvals are supported on GitHub and GitLab")
            state["approval_channel"] = args.approval_channel
        write_json(root, F_VCS, state)
        rules = read_json(root, F_RULES)
        rules["target_branch"] = branch
        rules.setdefault("setup_state", {})["host"] = args.host
        write_json(root, F_RULES, rules)
        audit_log(root, "vcs_host_selected", args.host, "recorded")
        print(f"Version control host: {HOSTS[args.host]['name']} (default branch {branch}, approvals via {state['approval_channel']})")
        return 0
    emit(state, True)
    return 0


def _host(root: Path) -> dict[str, Any]:
    state = vcs_load(root)
    if not state.get("host"):
        raise AnposError("choose the version control host first (`vcs detect`, then `vcs select <host>`)")
    return state


# ---------------------------------------------------------------- desired branch rules

PROFILES = {
    "team": {"required_approving_reviews": 1, "require_code_owner_review": True,
             "require_approval_of_most_recent_reviewable_push": True},
    "solo": {"required_approving_reviews": 0, "require_code_owner_review": False,
             "require_approval_of_most_recent_reviewable_push": False},
}


def desired_rules(root: Path, profile: str, checks: list[str]) -> dict[str, Any]:
    policy = read_json(root, F_RULES)
    base = dict(policy.get("rules") or {})
    base.update(PROFILES[profile])
    base["required_status_checks"] = checks
    base["require_status_checks"] = bool(checks)
    return {"profile": profile, "branch": policy.get("target_branch") or "main", "rules": base,
            "merge_policy": policy.get("merge_policy") or {}, "repository_settings": policy.get("repository_settings") or {}}


def successful_checks(root: Path, state: dict[str, Any]) -> tuple[list[str], str]:
    """Only checks that already ran successfully may become required (upstream quality rule)."""
    host, branch = state["host"], state.get("default_branch") or "main"
    try:
        if host == "github":
            data = gh_json(f"repos/{state['repository']}/commits/{branch}/check-runs?per_page=100")
            names = sorted({r["name"] for r in data.get("check_runs", []) if r.get("conclusion") == "success"})
            return names, "from successful GitHub check runs on " + branch
        if host == "gitlab":
            pid = quote(state["repository"], safe="")
            pipes = glab_json(f"projects/{pid}/pipelines?ref={branch}&status=success&per_page=1")
            if not pipes:
                return [], "no successful pipeline on " + branch + " yet"
            jobs = glab_json(f"projects/{pid}/pipelines/{pipes[0]['id']}/jobs?scope[]=success")
            return sorted({j["name"] for j in jobs}), "from the last successful GitLab pipeline"
    except AnposError as exc:
        return [], f"could not read checks ({exc})"
    return [], "not readable on this host; add required checks in the host UI after they pass once"


def cmd_rules_plan(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = _host(root)
    checks, source = successful_checks(root, state) if HOSTS[state["host"]]["auto"] else ([], "manual host")
    if args.check:
        checks = sorted(set(checks) | set(args.check)) if args.include_unverified else checks
    desired = desired_rules(root, args.profile, checks)
    ready, detail = cli_ready(HOSTS[state["host"]]["cli"]) if HOSTS[state["host"]]["auto"] else (False, "guided host")
    emit({"host": state["host"], "automation": "auto" if ready else "guide", "detail": detail,
          "required_checks_source": source, **desired,
          "profiles": {"team": "1 approving review + code owners (needs a second person)",
                       "solo": "PR + checks + no force-push/deletion, no review requirement (one-person projects)"}}, True)
    return 0


def cmd_rules_propose(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = _host(root)
    auto = HOSTS[state["host"]]["auto"] and cli_ready(HOSTS[state["host"]]["cli"])[0]
    checks, source = successful_checks(root, state) if auto else ([], "manual host")
    desired = desired_rules(root, args.profile, checks)
    r = desired["rules"]
    readable = [
        f"host {HOSTS[state['host']]['name']} ({'applied automatically and verified' if auto else 'step-by-step guide, you apply it'})",
        f"branch {desired['branch']}: pull request required, {r['required_approving_reviews']} approving review(s)"
        + (", code-owner review" if r["require_code_owner_review"] else ""),
        "dismiss stale reviews, resolve conversations, linear history, no force-push, no branch deletion",
        "required checks: " + (", ".join(checks) if checks else "none yet (only checks that already passed can be required)"),
        "squash merge only, delete branch after merge",
    ]
    return core.anpos_team_propose(root, "Apply branch rules", f"Profile {args.profile} for {state['host']}.", readable,
                                   "rules_apply", {"host": state["host"], "auto": auto, "desired": desired,
                                                   "checks_source": source, "repository": state.get("repository")})


def _apply_rules(root: Path, payload: dict[str, Any], record: dict[str, Any]) -> None:
    host, desired = payload["host"], payload["desired"]
    policy = read_json(root, F_RULES)
    setup = policy.setdefault("setup_state", {})
    setup.update({"user_decision": f"approved {desired['profile']} via {record['id']}", "host": host,
                  "profile": desired["profile"], "decided_at": iso(now())})
    if payload.get("auto") and host == "github":
        result = apply_github(payload["repository"], desired)
    elif payload.get("auto") and host == "gitlab":
        result = apply_gitlab(payload["repository"], desired)
    else:
        result = {"status": "manual_guide_generated", "guide": write_guide(root, host, desired), "verified": False}
    setup.update({"application_status": result["status"], "enforcement_verified": bool(result.get("verified")),
                  "verification": result.get("verification"), "guide": result.get("guide"),
                  "host_object": result.get("host_object"), "last_attempt_at": iso(now()),
                  "applied_checks": desired["rules"].get("required_status_checks") or []})
    policy["status"] = "applied_verified" if result.get("verified") else (
        "manual_setup_pending" if result["status"] == "manual_guide_generated" else "apply_failed")
    write_json(root, F_RULES, policy)
    audit_log(root, "branch_rules_apply", f"{host}:{desired['branch']}", result["status"], record["id"],
              risk="elevated", tool=host)


def github_ruleset(desired: dict[str, Any]) -> dict[str, Any]:
    r = desired["rules"]
    rules: list[dict[str, Any]] = [{"type": "deletion"}, {"type": "non_fast_forward"}]
    if r.get("require_linear_history"):
        rules.append({"type": "required_linear_history"})
    rules.append({"type": "pull_request", "parameters": {
        "required_approving_review_count": int(r.get("required_approving_reviews") or 0),
        "dismiss_stale_reviews_on_push": bool(r.get("dismiss_stale_reviews_on_push")),
        "require_code_owner_review": bool(r.get("require_code_owner_review")),
        "require_last_push_approval": bool(r.get("require_approval_of_most_recent_reviewable_push")),
        "required_review_thread_resolution": bool(r.get("require_conversation_resolution")),
    }})
    if r.get("required_status_checks"):
        rules.append({"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": bool(r.get("require_branch_up_to_date")),
            "required_status_checks": [{"context": c} for c in r["required_status_checks"]],
        }})
    return {"name": RULESET_NAME, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": rules}


def apply_github(repo: str, desired: dict[str, Any]) -> dict[str, Any]:
    info = gh_json(f"repos/{repo}")
    if not (info.get("permissions") or {}).get("admin"):
        return {"status": "insufficient_permission", "verified": False,
                "verification": "your GitHub account is not an admin of this repository"}
    body = github_ruleset(desired)
    existing = [r for r in gh_json(f"repos/{repo}/rulesets") if r.get("name") == RULESET_NAME]
    try:
        if existing:
            saved = gh_json("-X", "PUT", f"repos/{repo}/rulesets/{existing[0]['id']}", "--input", "-", stdin=json.dumps(body))
        else:
            saved = gh_json("-X", "POST", f"repos/{repo}/rulesets", "--input", "-", stdin=json.dumps(body))
    except AnposError as exc:
        return {"status": "host_refused", "verified": False,
                "verification": f"GitHub refused the ruleset (plan/visibility may not support it): {exc}"}
    mp = desired.get("merge_policy") or {}
    settings = ["-X", "PATCH", f"repos/{repo}",
                "-F", f"delete_branch_on_merge={str(bool((desired.get('repository_settings') or {}).get('delete_branch_on_merge', True))).lower()}",
                "-F", f"allow_squash_merge={str(bool(mp.get('allow_squash_merge', True))).lower()}",
                "-F", f"allow_merge_commit={str(bool(mp.get('allow_merge_commit', False))).lower()}",
                "-F", f"allow_rebase_merge={str(bool(mp.get('allow_rebase_merge', False))).lower()}"]
    gh_json(*settings)
    problems = verify_github(repo, desired, saved.get("id"))
    return {"status": "applied_verified" if not problems else "applied_with_drift", "verified": not problems,
            "verification": problems or "re-read from GitHub: ruleset and merge settings match",
            "host_object": f"ruleset {saved.get('id')}"}


def verify_github(repo: str, desired: dict[str, Any], ruleset_id: Any = None) -> list[str]:
    if ruleset_id is None:
        found = [r for r in gh_json(f"repos/{repo}/rulesets") if r.get("name") == RULESET_NAME]
        if not found:
            return ["ruleset not found on GitHub"]
        ruleset_id = found[0]["id"]
    live = gh_json(f"repos/{repo}/rulesets/{ruleset_id}")
    want = github_ruleset(desired)
    problems = []
    if live.get("enforcement") != "active":
        problems.append(f"ruleset enforcement is {live.get('enforcement')}")
    live_rules = {r["type"]: r.get("parameters") for r in live.get("rules", [])}
    for rule in want["rules"]:
        if rule["type"] not in live_rules:
            problems.append(f"missing rule {rule['type']}")
        elif rule.get("parameters"):
            for k, v in rule["parameters"].items():
                if k == "required_status_checks":
                    got = sorted(c.get("context") for c in (live_rules[rule["type"]] or {}).get(k, []))
                    if got != sorted(c["context"] for c in v):
                        problems.append(f"required checks differ: {got}")
                elif (live_rules[rule["type"]] or {}).get(k) != v:
                    problems.append(f"{rule['type']}.{k} is {(live_rules[rule['type']] or {}).get(k)}, expected {v}")
    info = gh_json(f"repos/{repo}")
    mp = desired.get("merge_policy") or {}
    for key in ("allow_merge_commit", "allow_rebase_merge", "allow_squash_merge"):
        if key in mp and info.get(key) is not None and info.get(key) != mp[key]:
            problems.append(f"repository setting {key} is {info.get(key)}")
    return problems


def apply_gitlab(path: str, desired: dict[str, Any]) -> dict[str, Any]:
    pid = quote(path, safe="")
    info = glab_json(f"projects/{pid}")
    perms = info.get("permissions") or {}
    level = max(int((perms.get("project_access") or {}).get("access_level") or 0),
                int((perms.get("group_access") or {}).get("access_level") or 0))
    if level < 40:
        return {"status": "insufficient_permission", "verified": False,
                "verification": "Maintainer (40) or Owner access is required on GitLab"}
    r, branch = desired["rules"], desired["branch"]
    run_cli("glab", "api", "-X", "DELETE", f"projects/{pid}/protected_branches/{quote(branch, safe='')}")
    glab_json("-X", "POST", f"projects/{pid}/protected_branches", "-f", f"name={branch}", "-f", "push_access_level=0",
              "-f", "merge_access_level=40", "-f", "allow_force_push=false")
    glab_json("-X", "PUT", f"projects/{pid}",
              "-f", f"merge_method={'ff' if r.get('require_linear_history') else 'merge'}",
              "-f", f"only_allow_merge_if_pipeline_succeeds={str(bool(r.get('required_status_checks'))).lower()}",
              "-f", f"only_allow_merge_if_all_discussions_are_resolved={str(bool(r.get('require_conversation_resolution'))).lower()}",
              "-f", "remove_source_branch_after_merge=true", "-f", "squash_option=default_on")
    notes = []
    if int(r.get("required_approving_reviews") or 0) > 0:
        code, out = run_cli("glab", "api", "-X", "POST", f"projects/{pid}/approval_rules", "-f", "name=ANPOS review",
                            "-f", f"approvals_required={int(r['required_approving_reviews'])}")
        if code != 0:
            notes.append("approval rules need GitLab Premium; set required approvals manually if your tier allows")
    problems = verify_gitlab(path, desired)
    return {"status": "applied_verified" if not problems else "applied_with_drift", "verified": not problems,
            "verification": (problems + notes) or "re-read from GitLab: protected branch and merge settings match",
            "host_object": f"protected branch {branch}"}


def verify_gitlab(path: str, desired: dict[str, Any]) -> list[str]:
    pid = quote(path, safe="")
    r, branch = desired["rules"], desired["branch"]
    problems = []
    try:
        pb = glab_json(f"projects/{pid}/protected_branches/{quote(branch, safe='')}")
    except AnposError:
        return [f"branch {branch} is not protected"]
    if pb.get("allow_force_push"):
        problems.append("force push is allowed")
    if any(int(a.get("access_level", 0)) != 0 for a in pb.get("push_access_levels", [])):
        problems.append("direct pushes are still allowed")
    info = glab_json(f"projects/{pid}")
    if r.get("require_linear_history") and info.get("merge_method") != "ff":
        problems.append(f"merge method is {info.get('merge_method')}")
    if r.get("required_status_checks") and not info.get("only_allow_merge_if_pipeline_succeeds"):
        problems.append("merge does not require a successful pipeline")
    return problems


GUIDE_STEPS = {
    "bitbucket": [
        "Repository settings → Workflow → Branch restrictions → Add a branch restriction for `{branch}`.",
        "Write access: nobody (changes only through pull requests). Turn off 'Allow rewriting branch history' and 'Allow deleting this branch'.",
        "Merge checks: minimum {reviews} approval(s){codeowners}; 'No changes requested'; 'All tasks resolved'; minimum 1 successful build{checks}.",
        "Repository settings → Merge strategies: allow only 'Squash'; enable 'Delete source branch after merge'.",
    ],
    "azure_devops": [
        "Project settings → Repositories → <repo> → Policies → Branch policies → `{branch}`.",
        "Require a minimum number of reviewers: {reviews}; reset votes on new pushes; {codeowners_ado}",
        "Check for comment resolution: Required. Limit merge types: Squash merge only.",
        "Build validation: add the pipeline that runs the ANPOS checks{checks}; policy requirement Required.",
        "Security: deny 'Force push (rewrite history and delete branches)' for Contributors on `{branch}`.",
    ],
    "gitea": [
        "Repository → Settings → Branches → Add branch protection rule for `{branch}`.",
        "Disable push (or whitelist only maintainers); disable force push; enable 'Block merge on rejected reviews' and 'Dismiss stale approvals'.",
        "Required approvals: {reviews}{codeowners}. Enable status check{checks} once the Actions workflow has passed.",
        "Repository → Settings → Repository: allow only 'Squash' merges and enable deleting the head branch after merge.",
    ],
    "other": [
        "Protect branch `{branch}`: no direct pushes, no force-push, no deletion.",
        "Require pull/merge requests with {reviews} approval(s){codeowners}; dismiss stale approvals on new commits; require resolved discussions.",
        "Require passing CI{checks}; require the branch to be up to date before merge.",
        "Allow only squash merges and delete source branches after merge.",
    ],
    "local": [
        "There is no hosting server, so branch rules can't be enforced by a host.",
        "Use `anpos quality local-hooks` to install a git pre-push hook that runs the ANPOS validator and refuses pushes to `{branch}` from other branches' work without review evidence.",
        "When you add a remote (GitHub, GitLab …), run `/anpos:govern` again to apply real rules.",
    ],
}


def write_guide(root: Path, host: str, desired: dict[str, Any]) -> str:
    r = desired["rules"]
    checks = r.get("required_status_checks") or []
    fmt = {
        "branch": desired["branch"], "reviews": r.get("required_approving_reviews", 0),
        "codeowners": " including code owners" if r.get("require_code_owner_review") else "",
        "codeowners_ado": "add the code-owner reviewers for ANPOS control-plane paths (see .github/CODEOWNERS)"
        if r.get("require_code_owner_review") else "no code-owner policy (solo profile)",
        "checks": (" (" + ", ".join(checks) + ")") if checks else " (choose the checks after they have passed once)",
    }
    steps = GUIDE_STEPS.get(host, GUIDE_STEPS["other"])
    text = [f"# Branch rules — {HOSTS.get(host, HOSTS['other'])['name']}", "",
            f"Profile **{desired['profile']}**, generated by the anpos plugin from `config/github/ruleset-policy.json` "
            f"on {iso(now())[:10]}. The plugin cannot apply these on this host automatically, so they are **not enforced** "
            "until you complete the steps below.", ""]
    text += [f"{i}. {s.format(**fmt)}" for i, s in enumerate(steps, 1)]
    text += ["", "When done, run `anpos rules confirm-manual --note \"<what you set>\"` so the setup is recorded as "
             "user-reported (it stays unverified by the plugin)."]
    rel = f"{GUIDES}/RULES-{host}.md"
    (root / GUIDES).mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("\n".join(text) + "\n", encoding="utf-8")
    return rel


def cmd_rules_verify(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = _host(root)
    policy = read_json(root, F_RULES)
    setup = policy.get("setup_state") or {}
    desired = desired_rules(root, setup.get("profile") or "team", list(setup.get("applied_checks") or []))
    if setup.get("application_status") not in ("applied_verified", "applied_with_drift"):
        problems = [f"rules are {setup.get('application_status') or 'not applied'} on {state['host']}"]
    elif state["host"] == "github":
        problems = verify_github(state["repository"], desired)
    elif state["host"] == "gitlab":
        problems = verify_gitlab(state["repository"], desired)
    else:
        problems = ["this host can't be verified by the plugin"]
    setup.update({"enforcement_verified": not problems, "last_verified_at": iso(now()), "drift": problems})
    policy["setup_state"] = setup
    write_json(root, F_RULES, policy)
    audit_log(root, "branch_rules_verify", state["host"], "ok" if not problems else "drift")
    emit({"host": state["host"], "enforced_and_matching": not problems, "problems": problems}, True)
    return 0 if not problems else 1


def cmd_rules_confirm_manual(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    policy = read_json(root, F_RULES)
    setup = policy.setdefault("setup_state", {})
    if setup.get("application_status") != "manual_guide_generated":
        raise AnposError("there is no pending manual rules guide to confirm")
    setup.update({"application_status": "user_reported_applied", "enforcement_verified": False,
                  "user_note": args.note, "user_reported_at": iso(now())})
    policy["status"] = "user_reported_unverified"
    write_json(root, F_RULES, policy)
    audit_log(root, "branch_rules_manual_confirmation", setup.get("host") or "?", "user_reported")
    print("Recorded as applied by the user (not verified by the plugin).")
    return 0


# ---------------------------------------------------------------- Code Quality

def cmd_quality(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    state = _host(root)
    quality = read_json(root, F_QUALITY)
    setup = quality.setdefault("setup_state", {})
    host = state["host"]
    if args.quality_cmd == "detect":
        result: dict[str, Any] = {"host": host}
        if host == "github" and cli_ready("gh")[0]:
            info = gh_json(f"repos/{state['repository']}")
            public = not info.get("private", True)
            ghas = ((info.get("security_and_analysis") or {}).get("advanced_security") or {}).get("status") == "enabled"
            result["capabilities"] = {"codeql": public or ghas, "dependency-review": public or ghas, "scorecard": public}
            result["basis"] = f"visibility {'public' if public else 'private'}, advanced security {'on' if ghas else 'off'}"
        elif host == "gitlab":
            result["capabilities"] = {"gitlab-sast": True, "gitlab-dependency-scanning": "Ultimate tier only"}
            result["basis"] = "GitLab SAST template is available on all tiers"
        else:
            result["capabilities"] = {}
            result["basis"] = "host-specific scanners are configured by hand; the baseline CI runs the ANPOS validator"
        setup["host"] = host
        setup["capability_detection"] = result
        setup["capability_detected_at"] = iso(now())
        quality["setup_state"] = setup
        write_json(root, F_QUALITY, quality)
        emit(result, True)
        return 0
    if args.quality_cmd == "apply":
        written: list[str] = []
        if host == "github":
            caps = (setup.get("capability_detection") or {}).get("capabilities") or {}
            enable = [c for c, ok in caps.items() if ok is True]
            unavailable = [c for c, ok in caps.items() if ok is False]
            cmd = [sys.executable, "scripts/install_quality_capabilities.py", "--apply"]
            if enable:
                cmd += ["--enable", *enable]
            if unavailable:
                cmd += ["--unavailable", *unavailable]
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AnposError("install_quality_capabilities failed: " + proc.stderr.strip())
            written = [f".github/workflows/{n}" for n in ("codeql-actions.yml", "dependency-review.yml", "scorecard.yml")
                       if (root / ".github/workflows" / n).exists()]
        else:
            written = write_ci(root, host, args.with_sast)
            quality = read_json(root, F_QUALITY)
            setup = quality.setdefault("setup_state", {})
            setup["host_ci_files"] = written
            setup["baseline_verification"] = "pending_first_host_run"
            write_json(root, F_QUALITY, quality)
        audit_log(root, "quality_baseline_install", host, "installed", tool=host)
        emit({"host": host, "files": written,
              "next": "commit and push, let the pipeline run once, then `rules propose` can require the passing checks"}, True)
        return 0
    if args.quality_cmd == "record-tool":
        tools = setup.setdefault("stack_tools", [])
        tools.append({"category": args.category, "tool": args.tool, "command": args.command, "rationale": args.rationale,
                      "recorded_at": iso(now())})
        setup["stack_specific_tooling"] = "configured"
        write_json(root, F_QUALITY, quality)
        print(f"recorded {args.category}: {args.tool}")
        return 0
    if args.quality_cmd == "local-hooks":
        hook = root / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\n# installed by the anpos plugin: block pushes when ANPOS validation fails\n"
                        "python3 scripts/validate_ai_native_repo.py || { echo 'ANPOS validation failed; push refused.' >&2; exit 1; }\n")
        hook.chmod(0o755)
        setup["local_hooks"] = ["pre-push: scripts/validate_ai_native_repo.py"]
        write_json(root, F_QUALITY, quality)
        print("installed .git/hooks/pre-push (local only; not shared through git)")
        return 0
    emit({"host": host, "setup_state": setup}, True)
    return 0


BASELINE_STEPS = ["python3 -m pip install -r requirements-anpos.txt", "python3 -m compileall -q scripts tests",
                  "python3 -m unittest discover -s tests -p 'test_*.py'", "python3 scripts/validate_ai_native_repo.py"]


def write_ci(root: Path, host: str, with_sast: bool) -> list[str]:
    files = []
    if host == "gitlab":
        job = ["anpos-repository-integrity:", "  image: python:3.12-slim", "  stage: test", "  script:"]
        job += [f"    - {s}" for s in BASELINE_STEPS]
        content = ["# ANPOS baseline quality gate (generated by the anpos plugin)"]
        if with_sast:
            content += ["include:", "  - template: Security/SAST.gitlab-ci.yml", ""]
        content += ["stages: [test]", ""] + job
        target = ".gitlab-ci.yml" if not (root / ".gitlab-ci.yml").exists() else "ci/anpos-quality.gitlab-ci.yml"
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text("\n".join(content) + "\n")
        files.append(target)
    elif host == "bitbucket":
        target = "bitbucket-pipelines.yml" if not (root / "bitbucket-pipelines.yml").exists() else "ci/anpos-bitbucket-pipelines.yml"
        content = ["# ANPOS baseline quality gate (generated by the anpos plugin)", "image: python:3.12", "pipelines:",
                   "  default:", "    - step:", "        name: anpos-repository-integrity", "        script:"]
        content += [f"          - {s}" for s in BASELINE_STEPS]
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text("\n".join(content) + "\n")
        files.append(target)
    elif host == "azure_devops":
        target = "azure-pipelines.yml" if not (root / "azure-pipelines.yml").exists() else "ci/anpos-azure-pipelines.yml"
        content = ["# ANPOS baseline quality gate (generated by the anpos plugin)", "trigger: [main]", "pr: [main]",
                   "pool:", "  vmImage: ubuntu-latest", "steps:", "  - task: UsePythonVersion@0",
                   "    inputs:", "      versionSpec: '3.12'"]
        content += [f"  - script: {s}" for s in BASELINE_STEPS]
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text("\n".join(content) + "\n")
        files.append(target)
    elif host == "gitea":
        src = root / ".github/workflows/repository-quality.yml"
        target = ".gitea/workflows/repository-quality.yml"
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text(src.read_text() if src.exists() else "")
        files.append(target)
    else:
        files.append("(no CI host) use `quality local-hooks`")
    return files


# ---------------------------------------------------------------- assurance (Req 83-96)

DETECT_HINTS = {
    "REQ-83": "the product calls an AI/LLM model", "REQ-84": "the product is user-facing",
    "REQ-85": "outcomes can be measured", "REQ-86": "a bounded experiment is justified",
    "REQ-87": "releases to real users (feature flags/canaries help)", "REQ-88": "always (major modules / releases)",
    "REQ-89": "AI makes or influences decisions about people", "REQ-90": "personal/regulated data or a regulated domain",
    "REQ-91": "always (architecture decisions)", "REQ-92": "AI models/prompts/tools are part of the product",
    "REQ-93": "public APIs, data contracts or dependencies with EOL", "REQ-94": "production operation with state",
    "REQ-95": "privileged automation (agents, rules, releases)", "REQ-96": "always (risks and exceptions)",
}


def cmd_assurance(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    doc = read_json(root, F_ASSURANCE)
    rows = {r["requirement_id"]: r for r in doc["requirements"]}
    if args.assurance_cmd == "status":
        emit([{"id": r["requirement_id"], "name": r["name"], "applicability": r["applicability"], "state": r["state"],
               "evidence": len(r["evidence_refs"]), "blocking": r["blocking_findings"],
               "applies_when": DETECT_HINTS.get(r["requirement_id"])} for r in doc["requirements"]], True)
        return 0
    row = rows.get(args.req)
    if not row:
        raise AnposError(f"unknown requirement {args.req} (REQ-83 … REQ-96)")
    if args.assurance_cmd == "classify":
        if args.applicability == "not_applicable":
            if not args.reason:
                raise AnposError("not_applicable needs a project-specific --reason")
            row.update(applicability="not_applicable", state="not_applicable", reason=args.reason)
        else:
            row.update(applicability=args.applicability, reason=args.reason or row.get("reason"))
            if row["state"] == "not_applicable":
                row["state"] = "not_started"
    elif args.assurance_cmd == "evidence":
        if args.ref not in row["evidence_refs"]:
            row["evidence_refs"].append(args.ref)
        if row["state"] == "not_started":
            row["state"] = "in_progress"
    elif args.assurance_cmd == "finding":
        if args.clear:
            row["blocking_findings"] = [f for f in row["blocking_findings"] if f != args.text]
        elif args.text not in row["blocking_findings"]:
            row["blocking_findings"].append(args.text)
            row["state"] = "blocked"
    elif args.assurance_cmd == "set-state":
        if row["applicability"] == "not_applicable":
            raise AnposError(f"{args.req} is not applicable; reclassify it first")
        if args.state == "passed":
            if not row["evidence_refs"]:
                raise AnposError("passed needs evidence first (`assurance evidence REQ-… --ref …`); policy files are never evidence")
            if row["blocking_findings"]:
                raise AnposError("clear or risk-accept the blocking findings first")
            row["last_verified_ref"] = args.verified_ref or core.git(root, "rev-parse", "HEAD", check=False) or None
            if not row["last_verified_ref"]:
                raise AnposError("commit first: passed is bound to a repository ref")
            row["last_verified_at"] = iso(now())
        row["state"] = args.state
    doc["status"] = "classified" if all(r["applicability"] != "pending_detection" for r in doc["requirements"]) \
        else "pending_assurance_classification"
    doc["last_reconciled_at"] = iso(now())
    doc["last_reconciled_ref"] = core.git(root, "rev-parse", "HEAD", check=False) or None
    write_json(root, F_ASSURANCE, doc)
    audit_log(root, f"assurance_{args.assurance_cmd}", args.req, row["state"])
    print(f"{args.req}: applicability {row['applicability']}, state {row['state']}")
    return 0


def assurance_release_problems(root: Path) -> list[str]:
    """Used by the release exit check."""
    if not (root / F_ASSURANCE).exists() or not core.is_full_child(root):
        return []
    problems = []
    for r in read_json(root, F_ASSURANCE)["requirements"]:
        if r["applicability"] == "pending_detection":
            problems.append(f"{r['requirement_id']} ({r['name']}): classify applicability (`assurance classify`)")
        elif r["applicability"] == "applicable" and r["state"] in ("failed", "blocked"):
            problems.append(f"{r['requirement_id']} is {r['state']}: " + "; ".join(r["blocking_findings"] or ["see evidence"]))
        elif r["applicability"] == "applicable" and r["state"] != "passed":
            problems.append(f"{r['requirement_id']} ({r['name']}) is {r['state']}: needs evidence and `set-state passed`")
    return problems


def cmd_adr(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    doc = read_json(root, F_ADR)
    decisions = doc.setdefault("decisions", [])
    if args.status not in doc.get("allowed_statuses", ["proposed", "accepted", "rejected", "superseded"]):
        raise AnposError(f"status must be one of {doc.get('allowed_statuses')}")
    new_id = f"ADR-{len(decisions) + 1:04d}"
    for old in args.supersedes or []:
        for d in decisions:
            if d.get("id") == old:
                d["status"] = "superseded"
                d["superseded_by"] = new_id
    decisions.append({
        "id": new_id, "title": args.title, "status": args.status, "context": args.context,
        "alternatives": args.alternative or [], "evidence_refs": args.evidence or [], "decision": args.decision,
        "consequences": args.consequence or [], "security_privacy_data_operational_cost_impact": args.impact or [],
        "reversibility": args.reversibility, "affected_modules_or_contracts": args.module or [],
        "decided_by": args.decided_by, "decided_at": iso(now()) if args.status == "accepted" else None,
        "supersedes": args.supersedes or [], "superseded_by": None,
    })
    doc["status"] = "active_registry"
    write_json(root, F_ADR, doc)
    audit_log(root, "adr_recorded", new_id, args.status)
    print(new_id)
    return 0


def cmd_ai_asset(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    doc = read_json(root, F_ASSETS)
    if args.secret_check and re.search(r"(sk-|api[_-]?key|token=|BEGIN [A-Z ]*PRIVATE KEY)", " ".join(
            filter(None, [args.name, args.version, args.config_hash, args.source]))):
        raise AnposError("that looks like a secret; the AI asset registry must never contain secrets")
    assets = doc.setdefault("assets", [])
    aid = f"AI-ASSET-{len(assets) + 1:04d}"
    assets.append({"id": aid, "type": args.type, "provider": args.provider, "name": args.name,
                   "version": args.version or "unknown (provider internal)", "configuration_hash": args.config_hash,
                   "source_or_artifact_ref": args.source, "environment_bindings": args.env or [],
                   "evaluation_dataset_version": None, "last_certified_evaluation_ref": None,
                   "last_certified_at": None, "contains_secret": False, "status": "unverified"})
    doc["status"] = "active_registry"
    write_json(root, F_ASSETS, doc)
    audit_log(root, "ai_asset_registered", aid, "unverified")
    print(aid)
    return 0


# ---------------------------------------------------------------- memory provenance

def cmd_memory(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    doc = read_json(root, F_MEMORY)
    entries = doc.setdefault("entries", [])
    if args.memory_cmd == "list":
        emit(entries[-args.limit:], True)
        return 0
    trust = args.trust or {"user": "trusted_authority", "repository": "verified_project_data",
                           "external": "untrusted_external_data"}[args.source_type]
    if args.source_type == "external" and trust == "trusted_authority":
        raise AnposError("external content can't be recorded as trusted authority; validate it first")
    mid = f"MEM-{len(entries) + 1:06d}"
    for old in args.supersedes or []:
        for e in entries:
            if e.get("id") == old:
                e["validation_status"] = "superseded"
    entries.append({"id": mid, "memory_target": args.target, "target_ref": args.target_ref, "value_summary": args.summary,
                    "source_type": args.source_type, "source_ref": args.source_ref, "source_hash_or_revision":
                    core.git(root, "rev-parse", "HEAD", check=False) or None if args.source_type == "repository" else None,
                    "recorded_by": "claude-code (anpos plugin)", "recorded_at": iso(now()), "trust_level": trust,
                    "validation_status": args.validation, "approval_ref": args.approval, "supersedes": args.supersedes or [],
                    "notes": args.note or []})
    write_json(root, F_MEMORY, doc)
    print(mid)
    return 0


# ---------------------------------------------------------------- continuous improvement

def cmd_watch(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    doc = read_json(root, F_WATCH)
    host = vcs_load(root).get("host")
    if args.watch_cmd == "enable":
        if not core.stack_is_approved(root):
            raise AnposError("the technology watch starts after the stack is approved (Stage 9)")
        doc.update({"enabled": True, "status": "active",
                    "scheduler": "github_actions" if host == "github" else "manual_or_claude_session"})
        write_json(root, F_WATCH, doc)
        audit_log(root, "technology_watch_enabled", host or "local", "enabled")
        print("Technology watch enabled" + ("" if host == "github" else " (no scheduler on this host: run /anpos:improve regularly)"))
        return 0
    if args.watch_cmd == "record":
        doc.update({"last_completed_at": iso(now()), "last_result": args.result, "last_request_reference": args.ref})
        write_json(root, F_WATCH, doc)
        audit_log(root, "technology_watch_completed", args.ref or "session", args.result)
        print("technology watch result recorded")
        return 0
    manifests = [p for p in ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml", "Gemfile",
                             "composer.json", "pom.xml", "build.gradle", "pubspec.yaml") if (root / p).exists()]
    due = True
    if doc.get("last_completed_at"):
        due = (now() - parse_time(doc["last_completed_at"])).total_seconds() > int(doc.get("interval_hours") or 24) * 3600
    emit({"enabled": doc.get("enabled"), "due": due, "dependency_manifests": manifests,
          "last_completed_at": doc.get("last_completed_at"), "update_classes": doc.get("update_classes"),
          "verification_after_update": doc.get("required_post_update_verification")}, True)
    return 0


def cmd_scout(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    require_full(root)
    doc = read_json(root, F_SCOUT)
    if args.scout_cmd == "enable":
        return core.anpos_team_propose(root, "Enable 25-hour Innovation Scout",
                                       "Periodic market/options/modules scouting with suggestions for the owner.",
                                       ["innovation scout: research every ~25h, suggestions only, nothing built without consent"],
                                       "scout_enable", {"enabled": True})
    if args.scout_cmd == "suggest":
        if not doc.get("enabled"):
            raise AnposError("the innovation scout is not enabled (`scout enable` needs APPROVE CHANGE)")
        bank = doc.setdefault("suggestions", [])
        sid = f"SUG-{len(bank) + 1:04d}"
        bank.append({"id": sid, "title": args.title, "rationale": args.rationale, "evidence_refs": args.evidence or [],
                     "status": "proposed", "proposed_at": iso(now())})
        write_json(root, F_SCOUT, doc)
        print(sid)
        return 0
    if args.scout_cmd == "record":
        doc.update({"last_completed_at": iso(now()), "last_result": args.result})
        write_json(root, F_SCOUT, doc)
        print("scout cycle recorded")
        return 0
    emit({k: doc.get(k) for k in ("enabled", "interval_hours", "last_completed_at", "last_result", "suggestions")}, True)
    return 0


def _apply_scout(root: Path, payload: dict[str, Any], record: dict[str, Any]) -> None:
    doc = read_json(root, F_SCOUT)
    doc.update({"enabled": True, "status": "active", "activation_consent_ref": record["id"]})
    write_json(root, F_SCOUT, doc)
    audit_log(root, "innovation_scout_enabled", "scout", "enabled", record["id"])


# ---------------------------------------------------------------- host-authenticated approvals

def open_host_approval(root: Path, record: dict[str, Any], phrase: str) -> str | None:
    """When approvals go through the host, open an issue that the owner answers with the phrase."""
    state = vcs_load(root)
    if state.get("approval_channel") != "host_comment":
        return None
    body = (f"**{record['title']}** ({record['id']})\n\n{record.get('summary') or ''}\n\n"
            + "\n".join(f"- {c}" for c in record.get("proposed_changes", []) if not str(c).startswith("action:"))
            + f"\n\nTo approve, a repository admin/maintainer comments exactly:\n\n`{phrase}`\n\n"
            f"Request hash `{record['request_hash'][:16]}…`, expires {record['expires_at']}.")
    if state["host"] == "github":
        code, out = run_cli("gh", "issue", "create", "-R", state["repository"], "--title",
                            f"ANPOS approval: {record['title']} ({record['id']})", "--body-file", "-", stdin=body)
        if code != 0:
            raise AnposError(f"could not open the GitHub approval issue: {out}")
        return out.splitlines()[-1].strip()
    if state["host"] == "gitlab":
        pid = quote(state["repository"], safe="")
        issue = glab_json("-X", "POST", f"projects/{pid}/issues", "-f",
                          f"title=ANPOS approval: {record['title']} ({record['id']})", "-f", f"description={body}")
        return issue.get("web_url")
    return None


def host_decision(root: Path, record: dict[str, Any], kind: str, code: str) -> dict[str, Any] | None:
    """Find an approve/reject comment by an authorized maintainer on the approval issue."""
    url = record.get("canonical_review_reference") or ""
    state = vcs_load(root)
    m = re.search(r"/(?:issues|-/issues)/(\d+)", url)
    if not m or state.get("approval_channel") != "host_comment":
        return None
    number = m.group(1)
    pattern = re.compile(rf"\b(APPROVE|REJECT)\s+{kind}\s+{code}\b", re.IGNORECASE)
    start, end = parse_time(record["requested_at"]), parse_time(record["expires_at"])
    if state["host"] == "github":
        comments = gh_json(f"repos/{state['repository']}/issues/{number}/comments?per_page=100")
        for c in comments:
            hit = pattern.search(c.get("body") or "")
            when = parse_time(c["created_at"])
            if not hit or not (start <= when <= end):
                continue
            login = (c.get("user") or {}).get("login")
            perm = gh_json(f"repos/{state['repository']}/collaborators/{login}/permission").get("permission")
            if perm in ("admin", "maintain"):
                return {"decision": hit.group(1).lower(), "login": login, "permission": perm,
                        "evidence": c.get("html_url"), "at": c["created_at"], "host": "github"}
    elif state["host"] == "gitlab":
        pid = quote(state["repository"], safe="")
        for n in glab_json(f"projects/{pid}/issues/{number}/notes?per_page=100"):
            hit = pattern.search(n.get("body") or "")
            when = parse_time(n["created_at"])
            if not hit or not (start <= when <= end) or n.get("system"):
                continue
            user = n.get("author") or {}
            member = glab_json(f"projects/{pid}/members/all/{user.get('id')}")
            if int(member.get("access_level") or 0) >= 40:
                return {"decision": hit.group(1).lower(), "login": user.get("username"),
                        "permission": f"access_level {member.get('access_level')}", "evidence": f"{url}#note_{n.get('id')}",
                        "at": n["created_at"], "host": "gitlab"}
    return None


# ---------------------------------------------------------------- CLI registration

def register(sub) -> None:
    v = sub.add_parser("vcs", help="version control host").add_subparsers(dest="vcs_cmd", required=True)
    v.add_parser("detect", help="suggest the host and show what can be automated").set_defaults(func=cmd_vcs)
    s = v.add_parser("select", help="record the host")
    s.add_argument("host", choices=sorted(HOSTS))
    s.add_argument("--default-branch")
    s.add_argument("--approval-channel", choices=["chat", "host_comment"])
    s.set_defaults(func=cmd_vcs)
    v.add_parser("show").set_defaults(func=cmd_vcs)

    r = sub.add_parser("rules", help="branch rules (any host: auto or guide)").add_subparsers(dest="rules_cmd", required=True)
    s = r.add_parser("plan", help="what would be applied")
    s.add_argument("--profile", choices=sorted(PROFILES), default="team")
    s.add_argument("--check", action="append")
    s.add_argument("--include-unverified", action="store_true")
    s.set_defaults(func=cmd_rules_plan)
    s = r.add_parser("propose", help="APPROVE CHANGE request; approval applies (auto) or writes the guide")
    s.add_argument("--profile", choices=sorted(PROFILES), default="team")
    s.set_defaults(func=cmd_rules_propose)
    r.add_parser("verify", help="re-read the host and report drift").set_defaults(func=cmd_rules_verify)
    s = r.add_parser("confirm-manual", help="record that you applied the guide yourself (stays unverified)")
    s.add_argument("--note", required=True)
    s.set_defaults(func=cmd_rules_confirm_manual)

    q = sub.add_parser("quality", help="Code Quality baseline per host").add_subparsers(dest="quality_cmd", required=True)
    q.add_parser("detect").set_defaults(func=cmd_quality)
    s = q.add_parser("apply")
    s.add_argument("--with-sast", action="store_true", help="GitLab: include the SAST template")
    s.set_defaults(func=cmd_quality)
    s = q.add_parser("record-tool", help="record a stack-specific tool after stack approval")
    s.add_argument("--category", required=True)
    s.add_argument("--tool", required=True)
    s.add_argument("--command", required=True)
    s.add_argument("--rationale")
    s.set_defaults(func=cmd_quality)
    q.add_parser("local-hooks", help="pre-push hook running the ANPOS validator").set_defaults(func=cmd_quality)
    q.add_parser("show").set_defaults(func=cmd_quality)

    a = sub.add_parser("assurance", help="Req 83-96 assurance state").add_subparsers(dest="assurance_cmd", required=True)
    a.add_parser("status").set_defaults(func=cmd_assurance)
    s = a.add_parser("classify")
    s.add_argument("req")
    s.add_argument("applicability", choices=["applicable", "not_applicable", "optional"])
    s.add_argument("--reason")
    s.set_defaults(func=cmd_assurance)
    s = a.add_parser("evidence")
    s.add_argument("req")
    s.add_argument("--ref", required=True, help="EVID-*, ADR-*, test report path, PR, commit …")
    s.set_defaults(func=cmd_assurance)
    s = a.add_parser("finding")
    s.add_argument("req")
    s.add_argument("text")
    s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_assurance)
    s = a.add_parser("set-state")
    s.add_argument("req")
    s.add_argument("state", choices=["in_progress", "blocked", "verification_required", "passed", "failed"])
    s.add_argument("--verified-ref")
    s.set_defaults(func=cmd_assurance)

    s = sub.add_parser("adr", help="record an architecture decision")
    s.add_argument("--title", required=True)
    s.add_argument("--context", required=True)
    s.add_argument("--decision", required=True)
    s.add_argument("--status", default="accepted")
    s.add_argument("--alternative", action="append")
    s.add_argument("--consequence", action="append")
    s.add_argument("--impact", action="append")
    s.add_argument("--evidence", action="append")
    s.add_argument("--module", action="append")
    s.add_argument("--reversibility", default="reversible")
    s.add_argument("--decided-by")
    s.add_argument("--supersedes", action="append")
    s.set_defaults(func=cmd_adr)

    s = sub.add_parser("ai-asset", help="register an AI model/prompt/tool configuration (no secrets)")
    s.add_argument("--type", required=True, choices=["model", "prompt", "tool", "retrieval", "embedding", "policy"])
    s.add_argument("--provider")
    s.add_argument("--name", required=True)
    s.add_argument("--version")
    s.add_argument("--config-hash")
    s.add_argument("--source")
    s.add_argument("--env", action="append")
    s.set_defaults(func=cmd_ai_asset, secret_check=True)

    au = sub.add_parser("audit", help="tamper-evident audit journal").add_subparsers(dest="audit_cmd", required=True)
    s = au.add_parser("append")
    s.add_argument("--action", required=True)
    s.add_argument("--target", required=True)
    s.add_argument("--result", required=True)
    s.add_argument("--consent")
    s.add_argument("--actor")
    s.add_argument("--role", default="claude-code")
    s.add_argument("--risk", default="normal")
    s.add_argument("--tool")
    s.add_argument("--result-ref")
    s.set_defaults(func=cmd_audit)
    au.add_parser("verify").set_defaults(func=cmd_audit)
    s = au.add_parser("list")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_audit)

    m = sub.add_parser("memory", help="durable memory with provenance").add_subparsers(dest="memory_cmd", required=True)
    s = m.add_parser("add")
    s.add_argument("--target", required=True, help="requirement / decision / research_fact / preference …")
    s.add_argument("--target-ref")
    s.add_argument("--summary", required=True)
    s.add_argument("--source-type", required=True, choices=["user", "repository", "external"])
    s.add_argument("--source-ref")
    s.add_argument("--trust", choices=["trusted_authority", "verified_project_data", "untrusted_external_data",
                                       "hostile_or_suspicious"])
    s.add_argument("--validation", default="unverified", choices=["unverified", "validated", "rejected", "superseded"])
    s.add_argument("--approval")
    s.add_argument("--supersedes", action="append")
    s.add_argument("--note", action="append")
    s.set_defaults(func=cmd_memory)
    s = m.add_parser("list")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_memory)

    w = sub.add_parser("watch", help="24-hour technology update watch").add_subparsers(dest="watch_cmd", required=True)
    w.add_parser("status").set_defaults(func=cmd_watch)
    w.add_parser("enable").set_defaults(func=cmd_watch)
    s = w.add_parser("record")
    s.add_argument("--result", required=True)
    s.add_argument("--ref")
    s.set_defaults(func=cmd_watch)

    sc = sub.add_parser("scout", help="25-hour innovation scout").add_subparsers(dest="scout_cmd", required=True)
    sc.add_parser("status").set_defaults(func=cmd_scout)
    sc.add_parser("enable", help="APPROVE CHANGE to activate").set_defaults(func=cmd_scout)
    s = sc.add_parser("suggest")
    s.add_argument("--title", required=True)
    s.add_argument("--rationale", required=True)
    s.add_argument("--evidence", action="append")
    s.set_defaults(func=cmd_scout)
    s = sc.add_parser("record")
    s.add_argument("--result", required=True)
    s.set_defaults(func=cmd_scout)


ACTIONS = {"rules_apply": _apply_rules, "scout_enable": _apply_scout}
