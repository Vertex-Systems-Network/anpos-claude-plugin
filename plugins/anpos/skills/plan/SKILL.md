---
name: plan
description: Build and approve the ANPOS project plan (Stage 7) — PRE-PLAN, options bank, modules bank, phases and small work units, requirements traceability and risks — then run the human "Approve Project Plan" gate. Use after research, when the user asks for a project plan/roadmap/breakdown, or to revise an existing ANPOS plan.
---

# ANPOS — Synthesize and Plan (Stage 7)

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language.
Before editing JSON, read `${CLAUDE_PLUGIN_ROOT}/reference/plan-files.md` (required fields and ID patterns).

Precondition: `lifecycle_stage` is `planning` (`anpos stage planning`). If the plan is already proposed/approved and the user wants a material change (scope, modules, critical path), run `anpos stage planning --revise` first; small refinements (splitting a work unit, clarifying criteria) do not need that.

## Ground the plan
Only plan after intake and research. The plan must rest on: user intent, `PROJECT-IDEA.md`, repository reality, `EVID-*` evidence, market comparison, lessons from comparable systems, feasibility, risks. Explicitly revise, add, remove, simplify or reprioritize capabilities when evidence justifies it, and say why.

## Build the artifacts (in this order)
1. **Requirements** — stable `REQ-001…` ids. Text goes in PRE-PLAN §5 (with source/EVID column); one link per requirement in `requirements-traceability.json` with `source` set.
2. **Options bank** — every capability considered as `OPT-*`: `selected`, `rejected`, `deferred` (each with `rationale`) or `candidate`.
3. **Modules bank** — coherent responsibilities `MOD-*`, with boundaries, dependencies, `option_ids`, acceptance criteria.
4. **Phases** — outcome-oriented `PHASE-*` in execution order, each with exit criteria. No giant phases.
5. **Work units** — small, independently verifiable slices `WU-*` (about one PR each) with phase, module, requirement ids, dependencies, priority, acceptance criteria, required checks, allowed paths. Then back-fill `work_unit_ids` / `module_ids` / `option_ids` on each traceability link.
6. **Risks** — material risks in `risk-register.json` with owner and mitigation.
7. **Product hypotheses** — PRE-PLAN §6: each user/problem hypothesis, how it will be validated, KPI when measurable.
8. **PRE-PLAN.md** — fill §1–§16, citing EVID ids, distinguishing: validated requirements · assumptions still to validate · recommended additions · removals/simplifications · market-driven requirements · differentiators · risks · unresolved decisions · phased priorities. Mirror the work-unit table (the JSON stays canonical).

For a large plan write it in passes (requirements → options/modules → phases/work units → PRE-PLAN), running `anpos validate` after each pass. Fix every ERROR; read every WARN and fix the ones that are real gaps (requirement without work unit, work unit without acceptance criteria, module in no phase).

## Approval gate — "Approve Project Plan"
1. `anpos consent propose --type plan_approval` → prints `consent_id`, `approve_phrase` (e.g. `APPROVE PLAN 3F9A1C2B`) and `reject_phrase`.
2. Present a compact summary in chat: objective, scope in/out, requirements count, modules, phases with their work units, key risks, unresolved decisions, and the main changes versus the user's original idea. Point to `docs/ai/PRE-PLAN.md` for detail.
3. Ask the user to type the approve phrase exactly, or the reject phrase, or describe changes (**Review Plan**). Never type the phrase yourself, never pre-fill it as their answer.
4. When they reply:
   - phrase typed → `anpos consent confirm`. On approval, update PRE-PLAN §17 (status approved, consent id, date) and `PROJECT-IDEA.md` Planning Status `COMPLETE`.
   - changes requested → edit the plan, `anpos validate`, and propose again (the old request is closed automatically).
   - `confirm` reports no captured decision → ask them to type the exact phrase (the plugin hooks must be enabled).
5. After approval, offer (once) to put the work units into the user's project tool. The repository stays the source of truth; ask before creating anything outside the repo.
   - Readable plan file: `anpos export --format md --output docs/ai/PLAN-EXPORT.md`
   - Spreadsheet / Linear / Jira / Trello CSV import: `anpos export --format csv --output docs/ai/plan-export.csv`
   - GitHub Issues: `anpos export --format issues` gives `{title, body, labels}` per work unit. Only if the user agrees and `gh` is installed and authenticated, create them with `gh issue create --title … --body … --label …` (create missing labels first with `gh label create`). Add each issue URL to the `artifact_refs` of the requirements that work unit covers in `requirements-traceability.json`, and tell the user the issue list.
   - A project management tool (Linear, Jira, GitHub Projects…) with real sync, mapping and status read-back → **pm** skill.

Then continue with the **lifecycle** skill (Stage 8 System Design).
