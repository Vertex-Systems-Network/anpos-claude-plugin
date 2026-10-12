# ANPOS Planner — Claude Code plugin (1.0.0, in development)

AI-native project planner based on **ANPOS 1.4.0** (`Vertex-Systems-Network/ai-native-project-operating-system`).
Free mode: is plugin mein koi subscription, licensing, Marketplace billing ya entitlement check nahi hai. Sab kuch user ki apni repository mein, locally chalta hai.

**Version policy:** version 1.0.0 hi rahega jab tak ANPOS ka har option plugin mein nahi ban jata (build 4 mein commercial licensing ke ilawa sab ban chuka hai; aap ke mock test ke baad final). Kya ban chuka hai aur kya baqi hai: [CHANGELOG.md](CHANGELOG.md).

## Kya karta hai

| Skill (slash command) | Kaam |
|---|---|
| `/anpos:start` | Repo detect karta hai aur do mein se ek mode lagata hai: **Full ANPOS** (poora control plane; purane code wale repo mein `anpos/adopt` branch par adoption) ya **Lite** (sirf planner files). Koi existing file overwrite nahi hoti |
| `/anpos:pm` | Project management tool chunna aur sync: GitHub (milestones + issues, `gh` se), ya Linear/Jira/Asana waghera unke connector se. Status wapas padhta hai; ikhtilaf conflict ban kar record hota hai, repo hi asal record rehta hai |
| `/anpos:intake` | Ek hi box mein idea, thoughts, research aur assumptions; phir requirements, facts, assumptions, constraints alag karta hai |
| `/anpos:research` | Stage 2–6: research, market comparison, competitors ka audit. Har finding `EVID-*` ID ke saath save hoti hai |
| `/anpos:plan` | Stage 7: PRE-PLAN, options bank, modules, phases, work units, traceability, risks. Phir **Approve Project Plan** gate |
| `/anpos:team` | **Choose Development AI** aur multi-agent: agent pool (Claude subagents, installed Codex/Gemini CLIs) aapki approval se register; budgets; aap ki session Supervisor, `anpos:worker` subagents alag git worktrees mein parallel kaam karte hain; review, merge, recovery. Stage 13 mein |
| `/anpos:lifecycle` | Stage 8–17 order mein, har stage ka exit check: system design (threat model, data records), **Approve Technology Stack**, architecture (ADRs), data flow, UI/UX, work unit by work unit development, QA, security, **Approve Release** (commit se bandha), operations |
| `/anpos:govern` | **Kisi bhi version control** ke liye governance: aap host chunte hain (GitHub, GitLab, Bitbucket, Azure DevOps, Gitea/Forgejo, doosra, ya sirf local). GitHub/GitLab par recommended branch rules aur Code Quality **khud apply aur verify**; baqi hosts par us host ki settings ka **step-by-step guide**. Host par issue comment ke zariye approvals (optional) |
| `/anpos:assure` | Req 83–96 assurance (AI evaluation, privacy, runbooks, risk…) evidence ke saath, ADRs, AI assets, audit journal, memory provenance, technology watch (24h) aur innovation scout (25h) |
| `/anpos:status` | Stage, progress, pending approval aur agla kaam |

Claude in skills ko khud bhi use kar leta hai jab user project planning ki baat kare.

### Human approval gates (asli, sirf prompt nahi)
- `consent propose` plan ka SHA-256 hash lekar ek pending consent banata hai aur ek phrase deta hai, jaise `APPROVE PLAN 3F9A1C2B`.
- Ye phrase **user ko khud type karna hota hai**. Plugin ka `UserPromptSubmit` hook sirf user ke bheje hue message se decision capture karta hai.
- `PreToolUse` guard hook Claude ko approval token likhne, consent file edit karne, ya `plan_status` ko khud `approved` karne se rokta hai.
- Approval ke baad plan badla to validator batata hai. Material change par `stage planning --revise` aur nayi approval.
- Stage gates: approved plan ke baghair Stage 8+ nahi, approved tech stack ke baghair Stage 10+ nahi, approved release candidate ke baghair operations nahi. Stages order mein chalti hain aur har stage ka exit check pass hona zaroori hai.
- Guard files padhne aur `git add/commit/diff` karne deta hai, taa ke approval evidence commit ho sake.
- Agent pool aur budgets bhi `APPROVE CHANGE` se hi badalte hain. Workers sirf apne worktree aur allowed paths mein likh sakte hain; ek hi command baar baar fail ho to circuit breaker rok deta hai.

### Plan export
Approved plan ke work units ko export karein: `export --format md` (readable plan), `--format csv` (Excel, Linear, Jira, Trello import), `--format issues` (GitHub Issues ke liye title/body/labels). `plan` skill approval ke baad ye khud offer karta hai; GitHub issues sirf user ki ijazat se bante hain.

### Branch rules — kis host par kya hota hai
| Host | Kya hota hai |
|---|---|
| GitHub (`gh` login, admin) | Ruleset API se apply, phir dobara padh kar verify (`applied_verified`). Plan support na kare (private repo, free plan) to saaf `host_refused` + guide |
| GitLab (`glab` login, Maintainer+) | Protected branch + merge settings (Premium par approval rule) apply aur verify |
| Bitbucket, Azure DevOps, Gitea/Forgejo, doosra | `docs/anpos/governance/RULES-<host>.md` mein us host ke screens ke hisaab se numbered steps; aap apply karke `rules confirm-manual` (unverified record) |
| Sirf local | Server nahi, is liye rules nahi; local pre-push hook |
Profiles: **team** (1 review + code owners) ya **solo** (PR + checks, review ke baghair — akele developer ke liye). Rules sirf `APPROVE CHANGE` ke baad lagte hain, aur sirf wahi checks required bante hain jo host par pehle pass ho chuke hon.

Hadd: approvals local session evidence hain (kis Claude Code session mein user ne phrase type kiya), aur agent identities plugin ki taraf se host-attested hain — dono cryptographic/authenticated identity nahi (zyada mazboot approvals ke liye GitHub/GitLab par `host_comment` channel). Guide wale hosts par rules plugin verify nahi kar sakta. Team mode ek machine par chalta hai (local git refs se locks).

## Install

Zaroorat: Claude Code aur Python 3.9+ (`python3`, `python` ya Windows par `py -3`, jo bhi mile). Windows par Git Bash chahiye (Claude Code wahan isi ko use karta hai). Optional: `pip install jsonschema` (full schema validation; iske baghair bhi cross-reference checks chalte hain).

**GitHub se (recommended):** is folder ko ek GitHub repo mein push karein, phir Claude Code mein:

```
/plugin marketplace add <owner>/<repo>
/plugin install anpos@anpos-marketplace
```

**Local folder se:**

```
claude plugin marketplace add /path/to/anpos-claude-plugin
claude plugin install anpos@anpos-marketplace
```

Phir kisi bhi project folder mein Claude Code kholein aur `/anpos:start` likhein, ya seedha apna idea bata dein.

Mock test ke liye step-by-step scenarios: [TESTING.md](TESTING.md). Tabdeeliyon ki list: [CHANGELOG.md](CHANGELOG.md).

Hatane ke liye: `claude plugin uninstall anpos@anpos-marketplace`. Project mein bani files waisi hi rehti hain.

## Repository ke saath compatibility

- **Khali repo, Full mode** → plugin ke andar bundled ANPOS child template (upstream commit `56b586b`, commercial service aur vendor files ke baghair) copy hota hai aur repo ka apna `bootstrap_child.py` chalta hai. Nateeja wahi hai jo upstream child ka hota hai; upstream validator aur upstream ke 121 tests pass hote hain.
- **Purane code wala repo, Full mode (adoption)** → `anpos/adopt` branch, nayi files add, `.gitignore` merge, `CLAUDE.md`/`AGENTS.md` mein ANPOS block, aur jo project files alag hain (README, CODEOWNERS, apne workflows) wo **waisi hi rehti hain**; ANPOS wala version `docs/anpos/adoption/` mein review ke liye, report `docs/anpos/ADOPTION-REPORT.md` mein.
- **Lite mode** → sirf planner files (`PROJECT-IDEA.md`, `docs/ai/PRE-PLAN.md`, `config/ai/*`, `config/research/evidence-registry.json`, `config/traceability/…`, `config/risk/…`, `config/consent/…`, `.anpos/`). Baad mein `init --full` se upgrade; plan aur approvals bache rehte hain.
- **ANPOS template ki copy (bootstrap nahi hui)** → pehle us repo ka apna `scripts/bootstrap_child.py` (dry run, phir `--apply`).
- **Full ANPOS child** → plugin wahi existing files use karta hai. Plugin ka banaya aur approve kiya hua plan upstream `scripts/validate_ai_native_repo.py` aur `scripts/plan_to_slots.py` dono pass karta hai (test kiya gaya).
- **Canonical source repo** → plugin mana kar deta hai (template inert rehna chahiye).

Same JSON schemas aur same consent hash format upstream ANPOS wale hain. Upstream ki kuch inert commercial-licensing policy files Full mode mein rehti hain kyunke upstream validator unhein maangta hai; plugin unhein kabhi use nahi karta.

## Folder structure

```
anpos-claude-plugin/
├── .claude-plugin/marketplace.json
├── README.md, TESTING.md, CHANGELOG.md
├── tools/build_template.py       # dev tool: bundled template ko upstream se dobara banana
└── plugins/anpos/
    ├── .claude-plugin/plugin.json
    ├── skills/{start,intake,research,plan,pm,lifecycle,team,govern,assure,status}/SKILL.md
    ├── agents/worker.md          # anpos:worker subagent (Worker protocol)
    ├── hooks/hooks.json, approval_capture.py, guard.py, breaker.py
    ├── scripts/anpos.py          # CLI: detect, init [--full], stage, stage-check, evidence, consent, validate, status, next, wu, sync-state, export, pm …, breaker
    ├── scripts/anpos_team.py     # agents …, team … (Supervisor/Worker, budgets)
    ├── scripts/anpos_govern.py   # vcs, quality, rules, assurance, adr, ai-asset, audit, memory, watch, scout
    ├── scripts/anpos.sh, pyrun.sh  # launcher: finds python3 / python / py -3
    ├── reference/cli.md, plan-files.md
    ├── schemas/                  # upstream ANPOS planner schemas (8)
    ├── templates/                # upstream planner file templates (lite mode)
    ├── template/anpos-child/     # bundled upstream child template (full mode) + SOURCE.json
    ├── tests/                    # 47 end-to-end tests (fake gh / glab, offline)
    └── evals/                    # claude plugin eval cases (skill triggering, no self-approval, honest PM answers)
```

## Development

```
python3 -m pytest plugins/anpos/tests -q
claude plugin validate ./plugins/anpos
claude plugin validate .
python3 tools/build_template.py /path/to/ai-native-project-operating-system   # naye upstream se template dobara banana
cd plugins/anpos && claude plugin eval . --allow-tools Bash Write Edit   # model runs; Linux needs bubblewrap + socat
```

Plugin ka naam, author aur repository link `plugins/anpos/.claude-plugin/plugin.json` aur `.claude-plugin/marketplace.json` mein hain. Apna GitHub repo banane ke baad wahan link badal dein, aur license chahiye to `LICENSE` file add karein.

## ChatGPT version (Phase 2) ke liye kya reuse hoga
- `scripts/anpos.py`, `schemas/`, `templates/` aur skills ka workflow text as-is reuse ho sakta hai.
- **Codex** (local files tak access): skills ko `AGENTS.md`/skills format mein adapt karna kaafi hai. Approval hook ki jagah Codex ka apna approval mechanism dekhna hoga.
- **ChatGPT app (web)**: ek MCP server chahiye jo `anpos.py` ke commands ko tools ke tor par expose kare aur GitHub OAuth se user ki repo mein feature branch/PR ke zariye likhe. Approval wahan ChatGPT ke confirmation UI se hoga.
