---
name: status
description: Show where an ANPOS project stands — lifecycle stage, plan approval, evidence and plan counts, pending approvals, the next ready work units and the next step. Use when the user asks for project status, progress, "what's next", or wants to resume ANPOS work.
---

# ANPOS — Status

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language.

1. Run `anpos status`. If it reports no ANPOS planner, offer the **start** skill.
2. Run `anpos validate` and note any errors.
3. If the plan is approved, run `anpos next`. In Stages 8–17 also run `anpos stage-check`; if a PM tool is connected, `anpos pm status` (mention open sync conflicts).
4. Quickly check repository reality against the recorded state (recent `git log`, work units marked in progress or complete). If they disagree, say so and propose the correction (`anpos wu <id> <status> --evidence …`, then `anpos sync-state`).

Answer briefly: current stage, plan status, progress, any pending approval (with the exact phrase the user must type), validation problems, and the single most useful next step. Don't paste raw JSON.
