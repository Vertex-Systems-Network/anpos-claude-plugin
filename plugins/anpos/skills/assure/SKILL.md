---
name: assure
description: ANPOS product and governance assurance (Requirements 83-96) plus continuous improvement — classify which assurance requirements apply, attach real evidence, record architecture decisions, AI assets, durable memory with provenance, and run the technology watch / innovation scout. Use when the user asks about AI evaluation, responsible AI, compliance evidence, ADRs, audit trail, project memory, dependency/technology updates, or release readiness of an ANPOS project.
---

# ANPOS — Assurance (Req 83–96), memory and continuous improvement

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language. Full ANPOS child required.

Rules: a policy or blueprint file is never evidence. `passed` needs real, project-specific evidence and is bound to a commit. Never state legal or compliance conclusions — collect evidence and say a qualified review is needed where it is.

## 1. Classify applicability (before release at the latest)
`anpos assurance status` lists REQ-83 … REQ-96 with a hint of when each applies (AI evaluation, user research, analytics, experiments, progressive delivery, engineering review, responsible AI, privacy/compliance, ADRs, AI asset registry, deprecation/EOL, runbooks/drills, audit journal, risk register). Decide with the user from the plan and design, then:
- `anpos assurance classify REQ-xx applicable`
- `anpos assurance classify REQ-xx not_applicable --reason "<project-specific reason>"`
- `optional` for experiments etc.
The release exit check refuses to proceed while any requirement is still `pending_detection`, or an applicable one is not `passed`.

## 2. Evidence → state
For each applicable requirement, produce the real artefact (evaluation report, usability notes, analytics plan + data, flag config, engineering review, risk entries, ADRs, runbook drill result…), commit it, then:
- `anpos assurance evidence REQ-xx --ref <EVID-… | ADR-… | path | PR | commit>`
- blocking issue: `anpos assurance finding REQ-xx "<what>"` (clear with `--clear` once fixed, or accept the risk through `consent propose --type scope_change`)
- `anpos assurance set-state REQ-xx passed` (refused without evidence or with open findings).

Helpers:
- Architecture decisions (REQ-91): `anpos adr --title … --context … --decision … [--alternative …] [--consequence …] [--supersedes ADR-…]`
- AI assets (REQ-92): `anpos ai-asset --type model|prompt|tool|retrieval|embedding|policy --name … [--provider] [--version] [--config-hash]` — never secrets; unknown provider versions stay "unknown".
- Risks (REQ-96): `config/risk/risk-register.json` (owner, mitigation, expiry for accepted risks).
- Audit journal (REQ-95): written automatically for privileged actions; `anpos audit verify`.

## 3. Memory with provenance
When you persist durable project knowledge (a decision, preference, validated fact), record where it came from: `anpos memory add --target decision --target-ref ADR-0002 --summary "…" --source-type user|repository|external [--source-ref …]`. External content stays `untrusted_external_data` until validated; use `--supersedes MEM-…` instead of silently overwriting.

## 4. Continuous improvement
- **Technology watch (every ~24h after stack approval):** `anpos watch enable` once. `anpos watch status` shows whether it's due and the dependency manifests. Research updates (patch/minor/major/runtime/security/deprecation) with sources (`anpos evidence add`), propose an update plan; code changes need consent (`consent propose --type scope_change`), then verify (build, tests, security…). Record the cycle: `anpos watch record --result "<summary>" [--ref <issue/PR>]`. On GitHub the installed workflows open a reminder issue on schedule; elsewhere the user runs `/anpos:assure` periodically.
- **Innovation scout (optional, ~25h):** activate with `anpos scout enable` (APPROVE CHANGE). Research the market/options, add suggestions (`anpos scout suggest --title … --rationale … --evidence EVID-…`), let the owner pick; promoting a suggestion into scope is a plan revision with consent. Record cycles with `anpos scout record --result …`.
