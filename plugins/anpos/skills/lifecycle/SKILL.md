---
name: lifecycle
description: Run the ANPOS post-plan engineering lifecycle (Stages 8-17) — system design, the "Approve Technology Stack" gate, architecture, data flow, UI/UX, incremental development by work unit, QA, security, the "Approve Release" gate and operations — with exit checks per stage. Use after the ANPOS plan is approved, or when the user asks to design, pick a stack for, build, test, release or operate an ANPOS project.
---

# ANPOS — Engineering Lifecycle (Stages 8–17)

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language.

Precondition: the plan is approved (`anpos status`). Stages run **in order**. `anpos stage <next>` only works when the current stage's exit check passes; `anpos stage-check` lists what is missing. `data_flow`, `ui_ux` and `operations` can be skipped only with `--not-applicable "reason"` (e.g. an API with no UI). Every move is logged in `.anpos/stage-log.json`.

Each stage writes a Markdown output in `docs/ai/` **and**, in a full ANPOS child, fills the structured files below (copy the `*_template` object inside each file; don't delete policy keys). Lite projects only need the Markdown. Run `anpos validate` after each stage — in full mode it also runs the project's own `scripts/validate_ai_native_repo.py`.

Cross-cutting: security, privacy, accessibility, observability, migration safety and rollback are designed early. Repository/test evidence beats any recorded status. Prefer the simplest architecture the evidence supports. Record important research with `anpos evidence add`.

| Stage | Markdown output | Structured files (full mode) — exit check |
|---|---|---|
| 8 `system_design` | `SYSTEM-DESIGN.md`: goals/non-goals, actors, boundaries, subsystems, integrations, trust boundaries, measurable NFRs, authn/authz/tenancy, privacy/compliance/accessibility, failure & recovery, environments, build-vs-buy | `config/security/threat-model.json`: `assets`, `actors`, `entry_points`, `threats` (from `threat_template`), `status: "active"` · `config/data/data-governance.json`: add `data_records` (from `data_record_template`; `[]` + `data_scope_note` if nothing is stored), `status: "active"` |
| 9 `technology_consent` | Stack comparison in `SYSTEM-DESIGN.md` or `ARCHITECTURE.md` | Gate: `anpos consent propose --type technology_stack --change "Frontend: …" --change "Backend: …" …` → user types `APPROVE STACK …` → `anpos consent confirm` (moves to `architecture`; reject → back to `system_design`) |
| 10 `architecture` | `ARCHITECTURE.md`: modules, contracts, persistence, jobs, config/secrets, observability, test architecture, CI/CD, release/rollback, migration approach | `config/architecture/decision-records.json`: at least one ADR (`decision_template`) with `status: "accepted"` |
| 11 `data_flow` | `DATA-FLOW.md`: flows, trust boundaries, authz checkpoints, retries, failure paths, retention/deletion | Every `confidential`/`restricted`/`secret` data record has `retention` and `deletion_method`. Breaking API/DB changes → `config/contracts/migration-policy.json` (`migration_template`) |
| 12 `ui_ux` | `UI-UX.md`: IA, journeys, screens, all states, components; WCAG 2.2 AA for web | `config/design/design-assurance.json`: `verification.responsive_targets`. External design (Figma etc.) → `config/design/design-intake.json` with revision/snapshot before approval |
| 13 `development` | — | All work units complete (or deferred) with evidence (see below) |
| 14 `sqa` | `QA-REPORT.md`: what ran, results, defects | `config/ai/project-state.json`: `qa_readiness: "ready"`, `critical_defects: []` |
| 15 `security` | `SECURITY-REVIEW.md`: tested surfaces, findings, severity, fixes, residual risk | `security_readiness: "ready"`; no high/critical threat left `open` (mark `mitigated`/`resolved`, or `accepted` with a `scope_change` consent) |
| 16 `release` | `RELEASE.md`: release notes, deployment plan, migration preflight, rollback/roll-forward, post-deploy smoke checks | `config/release/release-policy.json`: `environments` (from `environment_template`). Assurance REQ-83…96 classified and applicable ones `passed` (**assure** skill). Commit everything, then gate: `anpos consent propose --type release_candidate --change "notes: …"` (binds the current commit) → user types `APPROVE RELEASE …` → `anpos consent confirm` (moves to `operations`) |
| 17 `operations` | `OPERATIONS.md`: SLOs, alerts, incident flow, backup/restore, provider outage, RTO/RPO | `config/operations/operations-policy.json` `recovery.rto`/`rpo`, or runbooks in `config/operations/runbooks-and-drills.json`. Then `anpos stage maintenance` |

## Stage 13 — Development
Work only from the approved plan, one work unit at a time:
1. `anpos next` → top ready unit; `anpos wu <id> in_progress`.
2. Implement only inside its `allowed_paths`; add/update tests; never commit secrets.
3. Run its `required_checks`; commit (and open a PR if the repo uses them).
4. `anpos wu <id> complete --evidence "<test command + result / commit / PR>"`. Code existing is not completion — evidence is.
5. Repeated failure → `anpos wu <id> blocked --blocker "<reason>"` and tell the user, instead of looping.
6. If a PM tool is connected, sync after each completed unit (**pm** skill).
**Parallel team mode (full ANPOS):** instead of doing units one by one yourself, the **team** skill registers a Development AI pool, sets budgets, and dispatches `anpos:worker` subagents into git worktrees as Supervisor (claims, leases, fenced coordination, review, merge).
If the work needs a scope change, stop and use `anpos consent propose --type scope_change --title … --change …`, or `anpos stage planning --revise` for a material re-plan.

## Assurance and governance (full mode)
- **Before the release gate**, every assurance requirement REQ-83 … REQ-96 must be classified, and each applicable one `passed` with real evidence bound to a commit — the release exit check enforces this. Use the **assure** skill (`anpos assurance status`). Record important decisions as ADRs with `anpos adr`, AI models/prompts with `anpos ai-asset`, and durable knowledge with `anpos memory add` (provenance).
- After the stack is approved, add the stack's tools (linter, tests, dependency audit, security scan) to CI and `anpos quality record-tool …`; once checks have passed on the host, the **govern** skill can make them required by the branch rules.
- After release: `anpos watch enable` (technology watch ~24h) and, if the owner wants, `anpos scout enable` (innovation scout, APPROVE CHANGE).

A started workflow or a deployment request is not a successful release — record the actual outcome. After each stage: `anpos validate`, `anpos sync-state`, a short update to the user, then the next stage.
