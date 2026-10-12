---
name: govern
description: Set up ANPOS repository governance on any version-control host — choose the host (GitHub, GitLab, Bitbucket, Azure DevOps, Gitea/Forgejo, local), install the Code Quality baseline, and apply recommended branch rules automatically where possible or as an exact step-by-step guide; also approvals through the host, and drift checks. Use when the user asks about branch protection, GitHub/GitLab rules, CI quality gates, code scanning, or repository governance for an ANPOS project.
---

# ANPOS — Repository governance (any host)

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language. Needs a full ANPOS child.

Never claim rules or checks are enforced unless the plugin verified them on the host. Policy files are the desired state, not proof.

## 1. Choose the version control host
`anpos vcs detect` suggests the host from the remote and shows what can be automated:
- **GitHub** (`gh` logged in, admin rights) and **GitLab** (`glab` logged in, Maintainer+) → automatic, verified.
- **Bitbucket, Azure DevOps, Gitea/Forgejo/Codeberg, other/self-hosted** → the plugin writes an exact guide for that host's settings screens; the user applies it.
- **Local only** (no remote) → no server to enforce rules; offer the local pre-push hook.
Confirm with the user, then `anpos vcs select <host> [--default-branch …]`. If they use GitHub/GitLab and want stronger approvals, add `--approval-channel host_comment` (see 4).

## 2. Code Quality baseline (no separate consent needed — normal, non-destructive tooling)
1. `anpos quality detect` → what the host/plan supports (GitHub: CodeQL / Dependency Review / Scorecard depend on visibility and Advanced Security).
2. `anpos quality apply [--with-sast]` → GitHub: installs only the supported workflows (upstream `install_quality_capabilities.py`); GitLab/Bitbucket/Azure/Gitea: generates the host's CI file running the ANPOS validator (never overwrites an existing CI file — it writes `ci/anpos-…` and tells you to include it); local: `anpos quality local-hooks`.
3. After the stack is approved, add stack tools (formatter, linter, type check, tests, dependency audit, security scan) to the CI and record each: `anpos quality record-tool --category linter --tool ruff --command "ruff check ."`.
4. Ask the user to push so the pipeline runs once — checks can only be *required* after they have passed.

## 3. Branch rules — "Apply Recommended Rules"
1. Ask which profile fits: **team** (1 approving review + code owners; needs a second person) or **solo** (PR + checks + no force-push/deletion, no review requirement — one-person projects, since hosts don't let you approve your own PR).
2. `anpos rules plan --profile …` → show the rules and which checks would be required (only ones that already passed).
3. `anpos rules propose --profile …` → user types `APPROVE CHANGE …` → `anpos consent confirm`. The approval itself applies the rules:
   - automatic hosts: applied through the API and **re-read to verify** (`applied_verified`), or recorded honestly as `insufficient_permission` / `host_refused` (e.g. plan doesn't support rulesets on private repos) — then offer the guide path;
   - guided hosts: `docs/anpos/governance/RULES-<host>.md` with numbered steps. Walk the user through it; when they say it's done, `anpos rules confirm-manual --note "…"` (stays *unverified*).
4. Later: `anpos rules verify` re-reads the host and reports drift (someone weakened a rule).

## 4. Host-authenticated approvals (optional, GitHub/GitLab)
With `approval_channel: host_comment`, every `consent propose` opens an issue on the host; a repository admin/maintainer comments the exact phrase there and `anpos consent confirm` verifies the commenter's identity and permission through the host API. Chat phrases no longer count for that repository. Use this when several people share the repo or for compliance.

## 5. Audit
Privileged actions (approvals, agent pool, budgets, rules, merges, leases) are appended to the hash-chained `config/audit/audit-journal.json`. `anpos audit verify` checks the chain; `anpos audit list` shows recent entries.
