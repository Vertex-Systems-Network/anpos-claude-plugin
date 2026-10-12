# ANPOS planner CLI

Run with `sh <plugin>/scripts/anpos.sh [--root DIR] <command>` (finds Python 3.9+; equivalent to `python3 <plugin>/scripts/anpos.py …`). Default root is the git top level of the current directory. Exit code 0 = ok, 1 = error (message on stderr).

## Setup
| Command | What it does |
|---|---|
| `detect` | JSON classification: `canonical_source`, `uninitialized_child`, `active_project` (full ANPOS), `planner_lite`, `not_anpos`, `empty_repository`, `partial_or_malformed` |
| `init [--project-name N]` | **Lite:** installs the 10 planner files that are missing. Never overwrites |
| `init --full [--dry-run] [--repository owner/name] [--github-owner H] [--project-name N] [--branch B \| --no-branch]` | **Full ANPOS child** from the bundled upstream template + the project's own `bootstrap_child.py`. Empty repo → full install. Existing code → adoption on branch `anpos/adopt` (clean tree required): new files added, `.gitignore` merged, ANPOS block appended to agent files, differing project files kept with the ANPOS copy in `docs/anpos/adoption/`, report in `docs/anpos/ADOPTION-REPORT.md`. Lite → full keeps the plan and approvals. Refuses the canonical source (exit 2) |
| `status [--brief]` | Mode, stage, counts, pending approvals, PM sync, ready work, exit check, next step |
| `validate [--json]` | Schema + cross-reference + cycle + gate checks; in full mode also runs the project's `scripts/validate_ai_native_repo.py` ("adoption follow-up" = the project's own kept files) |

## Planning
| Command | What it does |
|---|---|
| `evidence add --claim … --source-type … --source-ref … --class … --confidence … --summary … [--title --publisher --published-at --req REQ-001 --opt OPT-X --mod MOD-X --note …]` | Appends the next `EVID-*` entry and prints its id |
| `consent propose --type plan_approval` | Validates, hashes the plan, creates a pending consent, sets plan `proposed`. Prints `approve_phrase` |
| `consent confirm [--id CONSENT-…]` | Records the decision the user typed (captured by the hook) |
| `export --format md\|csv\|issues [--output FILE] [--only-open]` | Work units as a readable plan, CSV, or GitHub-issue JSON |

## Lifecycle (Stages 8–17)
| Command | What it does |
|---|---|
| `stage <stage> [--revise] [--not-applicable REASON]` | Moves `lifecycle_stage`. After plan approval stages run in order and the current stage's exit check must pass. `--not-applicable` is allowed for `data_flow`, `ui_ux`, `operations`. `plan_proposed`/`plan_approved` are CLI-only. `--revise` returns an approved plan to `planning` |
| `stage-check [stage] [--json]` | What still blocks leaving the stage (docs in `docs/ai/`, and in full mode threat model, data records, ADRs, readiness flags, environments, recovery targets) |
| `consent propose --type technology_stack --change "Frontend: …" …` | Stage 9 gate (system design must pass first). Approve → `architecture` |
| `consent propose --type release_candidate [--change "notes: …"]` | Release gate in stage `release`; binds the current commit (clean tree required). Approve → `operations` |
| `consent propose --type scope_change --title … --change …` | Any other material change (scope, risk acceptance, GitHub Rules…) |
| `next [--limit N] [--json]` | Ready work units of the approved plan |
| `wu <WU-ID> <status> [--evidence …] [--blocker …]` | Updates a work unit; `complete` needs evidence; dependency order enforced |
| `sync-state` | Recomputes progress / next work unit in `project-state.json` |

## Project management sync (`config/integrations/pm-sync-state.json`)
| Command | What it does |
|---|---|
| `pm providers` | Catalog + how each can connect (GitHub via `gh`; others via a connector/MCP in the session) |
| `pm select <id> [--workspace-id/--workspace-name] [--project-id/--project-name/--project-url] [--verified] [--switch]` | Records the provider and mapping; mirrored into `project-management.json` / `linear-sync.json` in full mode |
| `pm skip` | Continue without a PM tool |
| `pm plan [--json] [--include-draft]` | create / update / archive actions for the approved plan (bodies carry an idempotency marker) |
| `pm apply [--dry-run]` | GitHub: milestones per phase, issues per work unit, close on completion, no duplicates |
| `pm record <ID> --external-id X [--url U] [--revision R]` | Record an item created/updated through a connector |
| `pm pull [--from-json FILE]` | Read provider states; disagreements become conflicts (repository is canonical) |
| `pm conflicts [--all] [--resolve ID --resolution TEXT]` | List / resolve sync conflicts |
| `pm status` | Provider, mapped items, pending changes, open conflicts |

## Development AI and team mode (full ANPOS)
| Command | What it does |
|---|---|
| `agents detect` | Agents this host can run: `claude-supervisor`, `claude-worker`, installed `codex`/`gemini`/`copilot`/`cursor-agent` CLIs |
| `agents select --supervisor ID --worker ID [--worker …] [--allowed-path P] [--denied-path P] [--ttl-days N]` | Proposes the pool (APPROVE CHANGE); `consent confirm` registers it with host-attested identity evidence in `.anpos/identity/` |
| `agents list` | Selected agents, roles, identity expiry |
| `team budgets show` / `team budgets propose --token-budget N --model-cost-budget X [--max-parallel-agents N …]` | Runtime limits (APPROVE CHANGE to change) |
| `team supervisor acquire\|heartbeat\|release [--agent-id ID] [--lease-minutes N]` | Epoch-fenced Supervisor lease (atomic local git ref `refs/anpos/supervisor/epoch-N`) |
| `team slots` | Slots from the approved plan (upstream `plan_to_slots.py` through the fenced gateway) |
| `team dispatch [--agent-id ID] [--slot S]` | Claim (upstream guard checks, budgets, atomic `refs/anpos/claims/…` lock) + worktree `.anpos/worktrees/<slot>` on branch `anpos/<WU>`; prints the brief |
| `team brief --slot S` | Worker instructions as text (for subagents or other CLIs) |
| `team heartbeat --slot S --agent-id ID` | Renew a worker lease |
| `team submit --slot S --agent-id ID --evidence TEXT` | Worker submission: lease valid, committed, diff inside allowed paths |
| `team review --slot S --approve \| --changes TEXT` | Supervisor decision (gateway transitions) |
| `team merge --slot S` | Merge into the base branch, complete the work unit with evidence, merge event, cleanup |
| `team release --slot S --reason TEXT [--cancel]` / `team recover` | Take a slot back / reopen expired leases |
| `team status` | Lease, slots, active workers, waiting submissions |
| `breaker status` / `breaker reset` | Circuit breaker for repeated identical failing commands |

## Repository governance — any version-control host (full ANPOS)
Hosts: `github`, `gitlab` (automatic + verified through `gh` / `glab`), `bitbucket`, `azure_devops`, `gitea`, `other` (exact guide written to `docs/anpos/governance/RULES-<host>.md`), `local` (no server; local pre-push hook). Host record: `config/integrations/vcs-host.json`.

| Command | What it does |
|---|---|
| `vcs detect` | Suggests the host from the git remote, shows CLI login status and what can be automated |
| `vcs select <host> [--default-branch B] [--approval-channel chat\|host_comment]` | Records the host. `host_comment` (GitHub/GitLab): approvals must be commented on a host issue by an admin/maintainer |
| `vcs show` | Current host record |
| `quality detect` | What the host/plan supports (CodeQL, Dependency Review, Scorecard, CI) |
| `quality apply [--with-sast]` | GitHub: supported workflows via the project's `install_quality_capabilities.py`; GitLab/Bitbucket/Azure/Gitea: CI file running the ANPOS validator (never overwrites an existing CI file); local: hook |
| `quality record-tool --category C --tool T --command CMD [--rationale R]` | Records a stack tool (after stack approval) |
| `quality local-hooks` / `quality show` | Pre-push hook running the validator / recorded baseline |
| `rules plan [--profile team\|solo] [--check NAME] [--include-unverified]` | Desired rules for the host; only checks that already passed become required |
| `rules propose [--profile …]` | `APPROVE CHANGE` request; `consent confirm` applies (auto hosts, then re-reads to verify) or writes the guide |
| `rules verify` | Re-reads the host; reports drift |
| `rules confirm-manual --note TEXT` | User applied the guide themselves (recorded as unverified) |

## Assurance, decisions, memory and continuous improvement (full ANPOS)
| Command | What it does |
|---|---|
| `assurance status` | REQ-83 … REQ-96 with applicability, state, evidence, findings |
| `assurance classify REQ-xx applicable\|not_applicable\|optional [--reason R]` | `not_applicable` needs a project-specific reason |
| `assurance evidence REQ-xx --ref REF` | EVID-*, ADR-*, report path, PR, commit |
| `assurance finding REQ-xx TEXT [--clear]` | Blocking finding |
| `assurance set-state REQ-xx in_progress\|blocked\|verification_required\|passed\|failed [--verified-ref COMMIT]` | `passed` needs evidence, no open findings; bound to a commit. The release exit check needs all REQ classified and applicable ones passed |
| `adr --title --context --decision [--alternative …] [--consequence …] [--supersedes ADR-…]` | Next `ADR-000N` in `config/architecture/decision-records.json` |
| `ai-asset --type model\|prompt\|tool\|retrieval\|embedding\|policy --name N [--provider --version --config-hash]` | AI asset registry entry; refuses anything that looks like a secret |
| `audit verify` / `audit list [--limit N]` / `audit append …` | Hash-chained `config/audit/audit-journal.json` (written automatically for approvals, agent pool, budgets, leases, merges, rules) |
| `memory add --target T --summary S --source-type user\|repository\|external [--source-ref R] [--supersedes MEM-…]` / `memory list` | Durable memory with provenance; external stays untrusted until validated |
| `watch enable\|status\|record --result R [--ref …]` | Technology watch (~24h, after stack approval) |
| `scout enable` (APPROVE CHANGE) / `scout status` / `scout suggest --title --rationale [--evidence EVID-…]` / `scout record --result R` | Optional innovation scout (~25h) |

## Approval protocol (never skip)
1. `consent propose …` prints e.g. `APPROVE PLAN 3F9A1C2B` (also `APPROVE STACK …`, `APPROVE RELEASE …`, `APPROVE CHANGE …`).
2. Summarize what is being approved and show the user the exact phrase. Ask them to type it (or the `REJECT …` phrase).
3. You must never type, paraphrase into, or simulate that phrase. The plugin's UserPromptSubmit hook only records text the user submits.
4. After the user's message, run `consent confirm`. If it says no decision was captured, ask the user to type the phrase exactly.
5. With `approval_channel: host_comment`, `consent propose` also opens an issue on the host (`approval_issue`). The phrase must be commented there by a repository admin/maintainer; `consent confirm` verifies the commenter through the host API and ignores chat phrases.
