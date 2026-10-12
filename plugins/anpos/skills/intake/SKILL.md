---
name: intake
description: Collect and normalize the user's raw project idea for ANPOS (Stage B7 and Stage 1 "Understand"). Use after ANPOS is initialized, or when the user shares a project idea, notes, research or assumptions to plan from.
---

# ANPOS — Intake and Understand

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language.

## 1. Collect (one field, no forms)

If `PROJECT-IDEA.md` has no raw input yet, ask exactly one open question:

> **Idea / Thoughts / Plan / Research / Search / Assumptions** — paste everything you have about the project in one message: raw idea, features, links, competitors, constraints, tech preferences, doubts. It does not need to be organized.

Do not force multiple required fields. If the user already gave the idea earlier in the conversation, use it instead of asking again. Files or links they attach are part of the intake.

## 2. Persist

- `anpos stage intake`
- In `PROJECT-IDEA.md`: put the user's words verbatim under **Raw User Input** (append later additions with a date; never rewrite away nuance). Set Intake Status `IN_PROGRESS`.

## 3. Understand (Stage 1)

Fill **Normalized Understanding** in `PROJECT-IDEA.md`, separating:
explicit requirements · facts · assumptions · preferences · constraints · existing research/references · open questions · risks/unknowns.

Never silently promote an assumption to a fact or requirement. Mark each item's origin when it is not obvious.

For each statement from the user that will drive a requirement, record evidence:
`anpos evidence add --claim "<claim>" --source-type user --source-ref "PROJECT-IDEA.md raw input" --class supported_inference --confidence medium --summary "<one line>"`

## 4. Clarify only what blocks research

Ask at most 3 short questions, and only for answers that change direction (target users, platform, must-have constraint, budget/timeline if decisive). Everything else becomes an explicit assumption to validate during research.

## 5. Hand off

Set Intake Status `COMPLETE`, run `anpos validate`, then `anpos stage research` and continue with the **research** skill. Tell the user in one or two lines what you understood and what research will look at.
