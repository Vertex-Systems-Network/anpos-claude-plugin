# Changelog

## 1.0.0 — in development

The version stays **1.0.0 until every ANPOS option is built**; there is no separate release before that. Free and local: no subscription, licensing or entitlement code. ANPOS protocol 1.4.0.

### Mock test round 1 (2026-10-12)
- **Competitive-superiority rule** (user request during the mock test): research Stage 6 is now a deep audit (official docs, help centers, pricing, store data-safety labels, complaints, local market players). The plan must be backed by `docs/ai/COMPETITIVE-ANALYSIS.md` (feature matrix with at least two competitors, citing EVID ids; stronger / weaker / not copied). `consent propose --type plan_approval` refuses without it; projects without a market can skip it only with `--no-market-comparison "<reason>"`, which is recorded in the consent. The plan skill shows the comparison before the approval phrase.
- Fix: `quality detect` on GitHub without `gh` login or before the repository exists now reports capabilities as unknown with the real reason instead of a misleading generic message.
- Docs: option `priority` is an integer (plan-files reference).
- **Fix (found when the user typed `APPROVE PLAN …`):** the approval hook only looked in the folder the Claude Code session was opened in, so a project in a subfolder silently lost the user's approval. `consent propose` now leaves a pointer (code → project, in `~/.claude/anpos/pending/`, removed on decision) and the hook also checks parent folders; a project is chosen only if it really has a pending request with that code. Still silent outside ANPOS projects.
- Fix: approving or rejecting the plan refreshes the project-state progress, so `validate` no longer warns "progress is out of date" right after approval.

### Added in build 4 (2026-10-12)
- **Any version-control host** (`vcs detect|select|show`, new `govern` skill): the user chooses GitHub, GitLab, Bitbucket, Azure DevOps, Gitea/Forgejo, other/self-hosted or local-only. The plugin automates what the host allows and guides the rest.
- **Branch rules ("Apply Recommended Rules")** from the upstream provider-neutral `ruleset-policy.json`, profiles `team` (1 review + code owners) and `solo` (PR + checks, no self-review deadlock). Behind `APPROVE CHANGE`; the approval itself applies them. GitHub: repository ruleset through `gh api` (no deletion, no force-push, linear history, PR, required checks); GitLab: protected branch + merge settings (+ approval rule on Premium) through `glab api`. Both are re-read and recorded as `applied_verified`, or honestly as `insufficient_permission` / `host_refused` with a guide fallback. Other hosts get an exact numbered guide in `docs/anpos/governance/RULES-<host>.md` and `rules confirm-manual` (stays unverified). Only checks that have already passed on the host become required. `rules verify` reports drift.
- **Code Quality baseline** (`quality detect|apply|record-tool|local-hooks`): GitHub through the project's `install_quality_capabilities.py` (only supported capabilities); GitLab/Bitbucket/Azure/Gitea CI files running the ANPOS validator (never overwrites an existing CI file); local pre-push hook; stack tools recorded after stack approval.
- **Host-authenticated approvals** (`vcs select … --approval-channel host_comment`): each `consent propose` opens a host issue; `consent confirm` accepts only the phrase commented by an admin/maintain (GitHub) or Maintainer+ (GitLab) user, verified through the host API.
- **Product and governance assurance, Req 83–96** (`assurance …`, new `assure` skill): classify applicability, attach evidence, findings, `passed` bound to a commit; the release exit check requires it.
- **ADRs** (`adr`, supersession), **AI asset registry** (`ai-asset`, secret refusal), **tamper-evident audit journal** (hash-chained, written automatically for approvals, agent pool, budgets, leases, merges, rules; `audit verify`), **memory with provenance** (`memory add|list`, external stays untrusted, supersession).
- **Continuous improvement**: technology watch (`watch enable|status|record`, after stack approval) and optional innovation scout (`scout enable` via APPROVE CHANGE, `suggest`, `record`).
- Tests: 47 end-to-end tests (new fake `glab`; fake `gh` extended for rulesets, comments, collaborators).

### Added in build 3 (2026-10-12)
- **Choose Development AI** (`agents detect|select|list`): detects what this machine can really run (Claude main session as Supervisor, the new `anpos:worker` subagent, installed Codex / Gemini / Copilot / Cursor CLIs). The pool is registered only after the user types `APPROVE CHANGE …`; the approved payload is part of the consent hash and is applied by `consent confirm`. Catalog entries get roles, capabilities, least-privilege permissions, explicit privacy unknowns and host-attested identity evidence (`.anpos/identity/`, with expiry).
- **Supervisor/Worker team mode** (`team …`, new `team` skill, `anpos:worker` agent): epoch-fenced Supervisor lease and atomic Worker claims as local git refs under `refs/anpos/` (the single-machine adapter the upstream scripts allow), authorization through the project's own `anpos_guard.py`, every shared write through the project's own `coordination_mutation.py` CAS gateway, slots from the upstream `plan_to_slots.py`. Workers get their own git worktree/branch; submission checks lease, commits and the worker's own diff against allowed/denied/protected paths; Supervisor review → merge completes the work unit with evidence, records the merge event and cleans up; release, recovery of expired leases and resuming a kept branch. Team mode runs in Stage 13 only.
- **Guard:** writes inside a worker worktree must match that slot's allowed paths and avoid protected control-plane paths; `anpos:worker` subagents can't write outside worktrees.
- **Runtime budgets** (`team budgets show|propose`): token and model-cost limits must be set (APPROVE CHANGE) before autonomous claims; `max_parallel_agents` and delegation depth enforced at dispatch (workers can't spawn agents).
- **Circuit breaker** (`hooks/breaker.py`, `breaker status|reset`): an identical failing shell command is blocked after `max_same_error_retries` failures.
- `validate` warns when a work unit's allowed paths overlap protected ANPOS paths (workers could not take it).
- Tests: 33 end-to-end tests; evals 5 cases, all passing. Live Claude Code runs: Supervisor dispatched two parallel `anpos:worker` subagents, reviewed, merged both (found and fixed: diffs now compare against the base branch so a worker may merge the base in); circuit breaker blocked the 4th identical failure; guard blocked a worker writing outside its worktree.

### Added in build 2 (2026-10-12)
- **Full ANPOS mode** (`init --full`): bundles the upstream child template (upstream commit `56b586b`, vendor-only paths removed) and runs the project's own `bootstrap_child.py`. Empty repositories get the complete control plane; existing code is adopted on branch `anpos/adopt` without overwriting any project file (`.gitignore` merged, ANPOS block appended to agent files, differing files kept with the ANPOS copy under `docs/anpos/adoption/`, adoption report). Lite projects upgrade in place and keep their plan and approvals. `validate` also runs the project's `validate_ai_native_repo.py` and separates adoption follow-ups from real errors.
- **Project management sync** (`pm …` commands, new `pm` skill): provider selection/mapping mirrored into the upstream `project-management.json` / `linear-sync.json`; GitHub milestones + issues through `gh` with idempotency markers; any other tool through its connector with `pm record`; status read-back where provider/repository disagreement becomes a recorded conflict (repository canonical); provider switching keeps external-id history.
- **Stage 8–17 exit checks** (`stage-check`, gated `stage`): stages run in order; each stage needs its `docs/ai/` output and, in full mode, its structured files (threat model, data records, ADRs, design assurance, readiness flags, release environments, recovery targets). `data_flow`, `ui_ux`, `operations` can be marked not applicable with a reason. Stage moves are logged in `.anpos/stage-log.json`.
- **Release gate** (`consent propose --type release_candidate`, phrase `APPROVE RELEASE …`) bound to a clean commit; approval moves to operations.
- Tests: 23 end-to-end tests (fake `gh` for offline sync tests); plugin evals now 4 cases (new: PM request without a plan is answered honestly), all passing. Live Claude Code run: adoption of an existing Express repo, plan approval, Stage 8 structured threat model + data records, stack gate.

### First build (2026-10-12)

- Skills: `start`, `intake`, `research`, `plan`, `lifecycle`, `status`.
- Planner CLI (`scripts/anpos.py`, launched through `scripts/anpos.sh`): `detect`, `init`, `stage`, `evidence add`, `consent propose|confirm`, `validate`, `status`, `next`, `wu`, `sync-state`, `export`.
- Human approval gates for the project plan (`APPROVE PLAN …`), technology stack (`APPROVE STACK …`) and scope changes (`APPROVE CHANGE …`), captured only from the user's own message by a `UserPromptSubmit` hook. Plan approval is bound to a SHA-256 digest of the plan content; consent records use the upstream ANPOS `consent_guard.py` hash format.
- `PreToolUse` guard: blocks Claude from writing approval tokens, editing consent records, or setting `plan_status: approved` by hand. Reading and `git add/commit/diff` of those files stays allowed; quoted text (commit messages) and heredoc bodies are not treated as commands.
- `SessionStart` hook: one-line status when the project uses ANPOS (silent elsewhere).
- Stage gates: Stage 8+ needs an approved plan, Stage 10+ needs an approved stack. Approving the stack moves the project to Stage 10 (architecture); rejecting it returns to system design.
- Validation: upstream Draft 2020-12 schemas (when `jsonschema` is installed) plus cross-references, dependency cycles, coverage warnings, consent tamper detection and post-approval change detection.
- Export of work units to Markdown, CSV and GitHub-issue JSON.
- Windows: hooks and skills use a POSIX launcher that finds `python3`, `python` or `py -3` (needs Git Bash, which Claude Code on Windows uses).
- Compatibility: plans built and approved with the plugin pass the upstream `validate_ai_native_repo.py` and are accepted by `plan_to_slots.py` in a bootstrapped ANPOS child.
- Tests: 15 pytest end-to-end tests; plugin eval suite in `plugins/anpos/evals/` (3 cases, all passing on Claude Code 2.1.296).

### Status
With build 4 every part of the ANPOS flow is implemented except the commercial licensing/subscription path, which is intentionally left out. The version stays 1.0.0 until the user's mock test is done.
