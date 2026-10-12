---
name: team
description: Run ANPOS multi-agent development — "Choose Development AI", register the agent pool, set runtime budgets, act as Supervisor, turn the approved plan into slots, dispatch Worker subagents into git worktrees in parallel, review, merge, and recover. Use when the user wants several agents (Claude subagents, Codex, Gemini…) to build an ANPOS project in parallel, or asks about the Supervisor/Worker setup, agent pool or budgets.
---

# ANPOS — Development AI and Supervisor/Worker team

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language. Needs a full ANPOS child (`anpos init --full`) and an approved plan.

You (this session) are the **Supervisor**. Workers are the plugin's `anpos:worker` subagents (or other installed CLIs) working in their own git worktrees. Identities are host-attested by this plugin after the human approves the pool — local evidence, not cryptographic identity; say so if asked.

## 1. Choose Development AI (once per repository)
1. `anpos agents detect` → what this machine can really run: `claude-supervisor`, `claude-worker`, and any installed `codex`, `gemini`, `copilot`, `cursor-agent` CLIs. Don't offer agents that aren't detected.
2. Recommend a pool (default: `--supervisor claude-supervisor --worker claude-worker`; add CLI workers only if the user wants them). Optional permission profile: `--allowed-path "src/**" --denied-path "infra/**"`.
3. `anpos agents select --supervisor … --worker … [--worker …]` → shows `APPROVE CHANGE …`. Explain what each agent may do (paths, no secrets, no deployment), ask the user to type the phrase, then `anpos consent confirm` (registers the pool and identity evidence).

## 2. Budgets (required before autonomous work)
`anpos team budgets show`. Token and model-cost budgets must be set (null = unconfigured, not unlimited). Propose with the user's numbers: `anpos team budgets propose --token-budget … --model-cost-budget … [--max-parallel-agents N]` → user types `APPROVE CHANGE …` → `anpos consent confirm`. Never raise limits without that approval.

## 3. Start a team run
1. Commit the main working tree.
2. `anpos team supervisor acquire` (epoch-fenced lease; renew with `team supervisor heartbeat` during long runs).
3. `anpos team slots` → one slot per work unit of the approved plan (dependencies respected at dispatch). Work units whose `allowed_paths` touch protected ANPOS paths (`tests/**`, `config/**`, `scripts/**` …) are refused for workers — fix the plan (e.g. use `src/**/tests/`) before dispatch.

## 4. Dispatch → work → submit (loop)
- `anpos team dispatch [--agent-id claude-worker] [--slot SLOT-…]` claims the highest-priority ready slot, creates its worktree/branch and prints the brief. Respect `max_parallel_agents`.
- **Claude workers:** start one `anpos:worker` subagent per dispatched slot (several in parallel when slots are independent), giving it the full brief JSON (or `anpos team brief --slot …`). Workers can't spawn further agents (delegation depth stays bounded).
- **CLI workers (codex/gemini/…):** dispatch with that agent id, check `<cli> --help` for its non-interactive mode, and run it inside the worktree with the text from `anpos team brief --slot …`. Afterwards verify its commits and run `team submit` for it only if the work is really done.
- The plugin guard blocks writes outside the worktree's allowed paths; `team submit` re-checks the full git diff.

## 5. Review and merge (only the Supervisor)
1. Read the submission (`.anpos/runtime/submissions/<slot>.json`) and the diff (`git -C <worktree> diff <base>..HEAD`). Run the required checks yourself when practical.
2. `anpos team review --slot … --approve` or `--changes "what to fix"` (the worker continues in the same worktree and submits again).
3. `anpos team merge --slot …` merges into the base branch, completes the work unit with evidence, records the merge event and cleans the worktree. A merge conflict sends the slot back with an alert.
4. Commit the updated ANPOS state; sync the PM tool if one is connected (**pm** skill).

## 6. Health
- `anpos team status` — lease, slots, active workers, submissions waiting.
- `anpos team recover` — reopens slots whose worker lease expired (the branch is kept; the next worker resumes it).
- `anpos team release --slot … --reason "…"` (or `--cancel`) when a slot must be taken back.
- The circuit breaker stops a command that failed the same way `max_same_error_retries` times; report the blocker (`anpos wu <id> blocked --blocker …`), don't loop. `anpos breaker status|reset` (reset only when the user agrees).
- When all slots are merged: `anpos team supervisor release`, then continue the lifecycle (**lifecycle** skill).
