---
name: research
description: Run ANPOS evidence-backed research (Stages 2-6) for a project — internet search for possibilities, deep research, independent reasoning, market comparison and comparable-system audits, recording every finding as an EVID entry. Use after intake, or when the user asks to research competitors, market or technology options for their ANPOS project.
---

# ANPOS — Research (Stages 2–6)

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language. Make sure `lifecycle_stage` is `research` (`anpos stage research`).

Purpose: discovery, not confirmation of the user's assumptions. Prefer current, primary, authoritative sources. Never base an important decision on one snippet.

If web search/fetch tools are not available in this session, say so, continue with the repository and the user's material, and mark market claims as `unknown` instead of guessing.

## Stage 2 — Search for possibilities
Look for: existing solutions and competitors, comparable products, user workflows/expectations, current technologies and approaches, APIs/platforms/libraries/standards, technical/regulatory/security/privacy/accessibility constraints, pricing patterns, known failure modes, and opportunities the user did not mention.

## Stage 3 — Research the strongest possibilities
Go deeper on the most relevant findings, compare alternatives and trade-offs.

## Stage 4 — Reason independently
Test assumptions against evidence; find missing pieces, contradictions, hidden dependencies, simpler/stronger alternatives, feasibility and scope risks; separate must-haves from optional; mark remaining uncertainty.

## Stage 5 — Compare with the market
Against direct/indirect competitors, category leaders, newer products, open-source substitutes: features and journeys, positioning, pricing, onboarding, integrations, platforms, trust/privacy/compliance/accessibility, recurring complaints. Say where the idea is stronger, weaker, undifferentiated, missing something important, or over-complicated.

## Stage 6 — Audit comparable systems
For the 2–4 most relevant systems: scope/users/jobs-to-be-done, feature and information architecture, key flows, roles/permissions, integrations, pricing, data/privacy/security signals, reliability, complaints, strengths to learn from, weaknesses to avoid, reusable decisions. Clearly separate verified evidence, reasonable inference, and unknown/private details.

## Recording evidence (mandatory)
Every material finding becomes an entry — one claim per entry:

```
anpos evidence add --claim "<claim or question>" --source-type primary|secondary|user|repository \
  --source-ref "<URL or path>" --title "<page title>" --publisher "<site/org>" --published-at "<date if known>" \
  --class verified_fact|supported_inference|unknown --confidence low|medium|high --summary "<your own words>"
```

- `verified_fact` only when the source directly states it; otherwise `supported_inference`.
- Contradicting sources: record both, then a note; do not silently overwrite (use `--supersedes` when a newer source replaces an older entry).
- Summaries are in your own words, never long quotes.
- Large research: you may split Stages 2–6 across parallel subagents by topic, but you record the evidence and do the synthesis yourself.

## Exit
- Update `PROJECT-IDEA.md`: Research Status `COMPLETE`, refresh open questions/risks.
- Give the user a short research brief (in chat): top findings with EVID ids, market position, recommended additions/removals, open decisions.
- `anpos validate`, then `anpos stage planning` and continue with the **plan** skill.
