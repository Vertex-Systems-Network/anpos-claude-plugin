# ANPOS planner files — field reference

All files are JSON validated against Draft 2020-12 schemas in the plugin's `schemas/` folder. Each file ships with a `*_template` object showing every field: copy it, fill it, append it to the array. Never delete the template/rule keys. Run `validate` after every batch of edits.

ID patterns: `OPT-…`, `MOD-…`, `PHASE-…`, `WU-…` use `[A-Z0-9][A-Z0-9-]*` after the prefix (e.g. `MOD-AUTH`, `WU-AUTH-2`). `REQ-001`, `EVID-000001` (6 digits), `RISK-0001`, `CONSENT-000001`.

## `PROJECT-IDEA.md`
Raw user input (verbatim, never paraphrased away) + normalized understanding: explicit requirements, facts, assumptions, preferences, constraints, references, open questions, risks. Status fields: `NOT_STARTED | IN_PROGRESS | COMPLETE | NEEDS_REVISION`.

## `config/research/evidence-registry.json` → `entries[]`
Add entries with the CLI (`evidence add`), not by hand. Required: `id, claim_or_question, source_type (primary|secondary|user|repository), source_ref, evidence_class (verified_fact|supported_inference|unknown), confidence (low|medium|high), summary, supports_requirement_ids, validation_status, supersedes, notes`. No extra keys allowed.

## `config/ai/options-bank.json` → `options[]`
Capabilities considered. Required: `id, name, category, description, status (candidate|selected|rejected|deferred|deprecated), requirement_source[], dependencies[] (OPT ids), conflicts[] (OPT ids)`. `priority` is an integer (higher first) or null — not a word like "high". `rationale` (non-empty string) is required when status is selected, rejected or deferred. Optional: `priority, technical_implications, ux_implications, data_implications, security_implications, qa_implications`.

## `config/ai/modules-bank.json` → `modules[]`
Required: `id, name, objective, scope[], boundaries[], dependencies[] (MOD ids), option_ids[], acceptance_criteria[], status`. Status: `not_started | ready | in_progress | blocked | needs_update | needs_removal | verification_required | complete | deferred | deprecated`. Optional: `data_entities, contracts, ui_surfaces, security_responsibilities, quality_gates, completion_evidence`.

## `config/ai/execution-plan.json`
- Leave `plan_status` and `plan_approval_ref` alone: the CLI manages them (the guard hook blocks setting `approved` by hand).
- `phases[]` required: `id, name, objective, module_ids[], dependencies[] (PHASE ids), status, exit_criteria[]`. Order the array in execution order.
- `work_units[]` required: `id, phase_id, module_id, requirement_ids[], title, objective, dependencies[] (WU ids), priority (integer, higher first), status, acceptance_criteria[], required_checks[], risk_classification (e.g. normal|elevated|high), allowed_paths[]`. When status is ready/in_progress/verification_required/complete, `acceptance_criteria`, `required_checks` and `allowed_paths` must be non-empty — fill them at planning time.
- Optional work-unit fields kept for full-ANPOS compatibility: `blockers, completion_evidence, eligibility (["ANY"]), required_roles (["worker"]), required_capabilities (["repository_write"]), denied_paths`.
- A work unit is small: one independently verifiable slice, normally one PR.
- In a full ANPOS child, `allowed_paths` must not overlap protected control-plane paths (`tests/**`, `scripts/**`, `config/**`, `.github/**`, `schemas/**` …) or team-mode workers can't take the unit; put project tests elsewhere (e.g. `src/**/tests/**`, `test/**`).

## `config/traceability/requirements-traceability.json` → `links[]`
One link per requirement. Required: `requirement_id, status (proposed|planned|…), verification_status (unverified|…)`. Fill `source` (an `EVID-*` id or "user intake"), `option_ids, module_ids, work_unit_ids`. Requirement text itself lives in PRE-PLAN §5 under the same `REQ-*` id.

## `config/risk/risk-register.json` → `risks[]`
Copy `risk_template`. `category` ∈ security, privacy, ai, product, operational, financial, compliance, architecture. `disposition` ∈ open, mitigating, accepted, resolved, expired_acceptance. Material risks need an `owner`.

## `config/consent/consent-requests.json`
Never edit. Managed only by `consent propose|confirm`.

## `docs/ai/COMPETITIVE-ANALYSIS.md`
Required before `consent propose --type plan_approval` (competitive-superiority rule): systems audited, a feature matrix (capability column + at least two competitors + this product, ✅/⚠️/❌/?), where we are stronger, where weaker, what we deliberately do not copy — citing `EVID-*` ids that exist in the registry. Skip only with `--no-market-comparison "<reason>"` for a project without a market.

## `docs/ai/PRE-PLAN.md`
Human-readable plan. Fill every `TBD` section from §1 to §16, cite `EVID-*` ids, mirror the work-unit table from `execution-plan.json` (the JSON is canonical). §8 (filled at the Stage 9 stack gate), §16 (decisions get resolved over time), §17 and §18 are not part of the approved content hash; every other section is.
