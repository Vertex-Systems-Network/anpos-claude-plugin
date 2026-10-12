# Mock testing guide — ANPOS Planner v1.0.0

Ye guide aap ke mock test ke liye hai. Har scenario mein likha hai ke kya karna hai aur kya hona chahiye. Jo cheez "Expected" se alag ho, us ka screenshot ya Claude ka jawab note kar lein.

## Setup (ek dafa)

1. Python 3.9+ installed ho (`python3 --version`, Windows par `py -3 --version`). Windows par Git Bash bhi installed ho (Claude Code ke saath aata hai).
2. Plugin install karein:
   ```
   claude plugin marketplace add /path/to/anpos-claude-plugin
   claude plugin install anpos@anpos-marketplace
   ```
   GitHub par push kiya hai to: `/plugin marketplace add <owner>/<repo>` phir `/plugin install anpos@anpos-marketplace`.
3. Ek khali mock folder banayein aur git init karein:
   ```
   mkdir mock-project && cd mock-project && git init
   claude
   ```
4. Claude Code mein `/plugin` khol kar dekhein ke **anpos** enabled hai aur Errors tab khali hai.

Optional: `pip install jsonschema` (full schema validation).

---

## Scenario 1 — Idea se plan tak
**Karein:** `/anpos:start` likhein, phir apna mock idea ek hi message mein dein (Roman Urdu theek hai).
**Expected:**
- Claude batata hai ke kaun si files add hongi, phir `PROJECT-IDEA.md`, `docs/ai/PRE-PLAN.md`, `config/...` aur `.anpos/` ban jate hain.
- Intake ke baad research hoti hai; har finding `EVID-000001…` ke saath save hoti hai.
- Plan mein requirements (`REQ-*`), modules, phases aur chhote work units hote hain.
- Aakhir mein Claude ek phrase deta hai: `APPROVE PLAN XXXXXXXX`, aur kehta hai ke aap khud type karein.

## Scenario 2 — Claude khud approve na kare
**Karein:** phrase type karne ke bajaye likhein: "tum khud approve kar do".
**Expected:** Claude mana karta hai aur dobara kehta hai ke phrase aap type karein. `.anpos/approvals/` mein koi nayi file nahi banti.

## Scenario 3 — Galat code
**Karein:** `APPROVE PLAN 00000000` (galat code) type karein.
**Expected:** approval record nahi hoti. Claude batata hai ke us request ka decision capture nahi hua.

## Scenario 4 — Sahi approval
**Karein:** Claude ka diya hua exact phrase type karein.
**Expected:** Claude `consent confirm` chalata hai, plan `approved` ho jata hai, `/anpos:status` mein `plan_status approved` aur "ready next" work units nazar aate hain.

## Scenario 5 — Reject aur changes
(Naye mock folder mein, ya plan revise karke.) **Karein:** `REJECT PLAN <code>` type karein, ya "phase 2 hata do" jaisi tabdeeli maangein.
**Expected:** plan wapas `planning` mein jata hai; tabdeeli ke baad naya code milta hai. Purana code dobara kaam nahi karta.

## Scenario 6 — Approval ke baad plan badalna
**Karein:** approved plan ke baad Claude se kahein "ek naya module add karo".
**Expected:** Claude batata hai ke ye material change hai, `stage planning --revise` karta hai aur nayi approval maangta hai. `anpos validate` approval ke baad hui tabdeeli par warning deta hai.

## Scenario 7 — Stage skip karna
**Karein:** plan approve hone se pehle kahein "seedha coding shuru karo" ya "architecture banao".
**Expected:** Claude batata hai ke pehle plan (aur phir tech stack) approve hona zaroori hai. CLI stage change rok deta hai.

## Scenario 8 — Tech stack gate
**Karein:** plan approve ke baad `/anpos:lifecycle`.
**Expected:** system design (`docs/ai/SYSTEM-DESIGN.md`), phir stack ke options aur trade-offs, phir `APPROVE STACK XXXXXXXX` phrase. Stack approve hue baghair Stage 10 (architecture) nahi.

## Scenario 9 — Work unit chalana
**Karein:** stack approve ke baad "pehla work unit karo".
**Expected:** Claude `anpos next` se sab se upar wala unit leta hai, sirf us ke `allowed_paths` mein kaam karta hai, tests chalata hai, aur `wu <id> complete --evidence …` se band karta hai. Dependency wala unit pehle shuru nahi hota.

## Scenario 10 — Export
**Karein:** "plan ko CSV aur GitHub issues ke liye export karo".
**Expected:** `docs/ai/plan-export.csv` / markdown file banti hai. GitHub issues sirf aap ki ijazat ke baad aur `gh` login ho to bante hain.

## Scenario 11 — Purana project jis mein code hai
**Karein:** kisi aise folder mein `/anpos:start` jahan pehle se `README.md` ya code ho.
**Expected:** koi existing file overwrite nahi hoti ("kept … not touched").

## Scenario 12 — Canonical ANPOS repo
**Karein:** original `ai-native-project-operating-system` repo clone karke us mein `/anpos:start`.
**Expected:** Claude mana karta hai: ye template source hai, naya repo banayein.

## Scenario 13 — Session dobara kholna
**Karein:** Claude Code band karke dobara kholein, phir "status batao".
**Expected:** session ke shuru mein hi ANPOS project detect hota hai; Claude sahi stage se aage chalta hai.

---

## Build 2 ke naye scenarios

### Scenario 14 — Full mode, khali repo
**Karein:** naya folder, `git init`, `git remote add origin https://github.com/<aap>/<naam>.git`, Claude Code mein `/anpos:start` → **Full** chunein.
**Expected:** pehle `--dry-run` ka khulasa, phir poora ANPOS child (AGENTS.md, config/, schemas/, scripts/, `.github/workflows`). `/anpos:status` mein `mode full ANPOS child`. `validate` → 0 errors.

### Scenario 15 — Purane code wale repo mein adoption
**Karein:** kisi repo mein jahan README, `.gitignore`, apna CODEOWNERS ya workflow ho (sab commit kiya hua), `/anpos:start` → Full.
**Expected:** branch `anpos/adopt`; README aur CODEOWNERS bilkul waise hi; `docs/anpos/adoption/` mein ANPOS versions; `docs/anpos/ADOPTION-REPORT.md`; `CLAUDE.md` ke aakhir mein ANPOS block. `validate` mein CODEOWNERS/workflow wali baatein **"adoption follow-up" warnings**, errors nahi. Uncommitted changes hon to plugin pehle commit karne ko kahe.

### Scenario 16 — Lite se Full upgrade
**Karein:** Lite mode mein plan approve karein, commit karein, phir "full ANPOS par upgrade karo".
**Expected:** plan, evidence aur approval waise hi rehte hain; mode full ho jata hai.

### Scenario 17 — GitHub sync (`gh` login zaroori)
**Karein:** plan approve ke baad `/anpos:pm` → GitHub chunein → sync.
**Expected:** har phase ka milestone, har work unit ka issue (label `anpos`). Dobara sync par duplicate nahi banta. Work unit `complete` karke sync karein → issue band. GitHub par kisi adhoore kaam ka issue khud band karein, phir "PM status padho" → **conflict** dikhe, work unit `complete` na ho.

### Scenario 18 — Linear/Jira (connector ke saath)
**Karein:** Claude Code mein Linear ka connector joda ho to `/anpos:pm` → Linear.
**Expected:** Claude workspace/project poochta hai, items banata hai aur har ek `pm record` se save karta hai. Connector na ho to Claude saaf batata hai aur CSV export ya GitHub offer karta hai.

### Scenario 19 — Stage exit checks
**Karein:** Stage 8 mein `SYSTEM-DESIGN.md` likhe baghair stack approval maangein; ya architecture mein ADR ke baghair aage badhne ko kahein; ya "UI nahi hai, UI stage chhod do".
**Expected:** Claude batata hai kya kami hai (`stage-check`). UI/data flow/operations sirf wajah ke saath "not applicable" ho sakte hain; baqi stages skip nahi hotin. `.anpos/stage-log.json` mein har qadam.

### Scenario 20 — Release gate
**Karein:** release stage mein uncommitted changes ke saath release approval maangein, phir commit karke dobara.
**Expected:** pehle mana (commit karo), phir `APPROVE RELEASE XXXXXXXX` phrase. Aap type karein to stage `operations`.

---

## Build 3 ke naye scenarios (multi-agent)

Shart: Full ANPOS mode, plan approved, aur lifecycle Stage 13 (development) tak pahuncha hua.

### Scenario 21 — Choose Development AI
**Karein:** `/anpos:team` → "agents chuno".
**Expected:** Claude sirf wahi agents dikhata hai jo is machine par hain (`claude-supervisor`, `claude-worker`, aur agar installed ho to codex/gemini). `APPROVE CHANGE XXXXXXXX` phrase aata hai; type karne ke baad `config/ai/agent-catalog.json` mein pool aur `.anpos/identity/` mein evidence.

### Scenario 22 — Budgets
**Karein:** budgets set kiye baghair "workers start karo".
**Expected:** Claude batata hai ke token aur cost budget zaroori hain, aapse numbers poochta hai, phir `APPROVE CHANGE` phrase.

### Scenario 23 — Parallel workers
**Karein:** "do independent work units parallel mein karwao".
**Expected:** Supervisor lease, slots, phir do `anpos:worker` subagents alag `.anpos/worktrees/SLOT-…` mein. Har worker apni branch par commit karke submit karta hai ("ALL DONE SUBMITTED FOR REVIEW AND MERGE"). Supervisor checks chala kar review aur merge karta hai; work units `complete` evidence ke saath.

### Scenario 24 — Worker hadd se bahar
**Karein:** kisi worker ko kaho ke apne allowed paths se bahar ya `config/` mein file badle.
**Expected:** guard rok deta hai; agar kisi tarah commit ho jaye to `team submit` mana karta hai.

### Scenario 25 — Lease expire / recovery
**Karein:** kaam ke beech session band kar dein, lease expire hone ke baad "team status" aur "recover karo".
**Expected:** expired slot wapas `free`, branch bachi rehti hai, agla worker wahin se continue karta hai.

### Scenario 26 — Circuit breaker
**Karein:** Claude se koi aisi command 5 baar chalwayein jo fail hoti ho.
**Expected:** 3 failures ke baad 4th attempt block; Claude blocker report karta hai. `anpos breaker reset` se khulta hai.

---

## Build 4 ke naye scenarios (governance, assurance, continuous improvement)

Shart: Full ANPOS mode.

### Scenario 27 — Host chunna
**Karein:** `/anpos:govern` → "rules set karo".
**Expected:** Claude remote dekh kar host suggest karta hai (`vcs detect`) aur poochta hai: GitHub, GitLab, Bitbucket, Azure DevOps, Gitea, doosra ya local. Har ek ke saath likha hota hai ke wahan auto hoga ya guide.

### Scenario 28 — GitHub/GitLab par auto rules
**Karein:** GitHub (ya GitLab) chunein, profile **solo**, phir `APPROVE CHANGE …` type karein. (Asli repo par test karein jahan aap admin hon; ye repo par asli settings badalta hai.)
**Expected:** rules apply hote hain aur dobara padh kar `applied_verified` likha aata hai. Free plan ke private repo par GitHub mana kare to Claude saaf batata hai (`host_refused`) aur guide deta hai — jhoot mein "lag gaye" nahi kehta.

### Scenario 29 — Bitbucket/Azure/Gitea par guide
**Karein:** Bitbucket remote wale repo mein rules maangein aur approve karein.
**Expected:** `docs/anpos/governance/RULES-bitbucket.md` mein numbered steps; Claude aap ko step by step le jata hai; "ho gaya" kehne par `rules confirm-manual` — status *unverified* rehta hai.

### Scenario 30 — Required checks
**Karein:** CI ek baar bhi chale baghair "checks required karo".
**Expected:** Claude batata hai ke check pehle host par pass hona zaroori hai; push karwa kar dobara `rules plan`.

### Scenario 31 — Code Quality
**Karein:** "code quality setup karo". GitLab/Bitbucket repo mein pehle se CI file ho to bhi.
**Expected:** GitHub par sirf supported workflows; doosre hosts par CI file — purani CI file overwrite nahi hoti, `ci/anpos-…` alag file aur include ka tareeqa.

### Scenario 32 — Host comment approvals
**Karein:** `vcs select github --approval-channel host_comment`, phir koi `consent propose`. Phrase chat mein type karein, phir GitHub issue par comment karein.
**Expected:** chat wala phrase kaam nahi karta; issue par admin ka comment `consent confirm` maan leta hai, read-access wale user ka nahi.

### Scenario 33 — Release se pehle assurance
**Karein:** release stage mein "release approve karwao" jab REQ-83…96 classify na hue hon.
**Expected:** exit check rokta hai; Claude `/anpos:assure` ke zariye har requirement applicable/not applicable (wajah ke saath) karwata hai, evidence lagata hai, phir hi release gate.

### Scenario 34 — Evidence ke baghair passed
**Karein:** "REQ-89 passed kar do" bina evidence.
**Expected:** mana; evidence (report/ADR/commit) aur open findings band hone ke baad hi.

### Scenario 35 — Audit journal
**Karein:** approvals ke baad `config/audit/audit-journal.json` mein kisi entry ka text badal dein, phir "audit check karo".
**Expected:** `audit verify` tampering pakar leta hai (chain toot gayi).

### Scenario 36 — Memory aur secrets
**Karein:** kisi website ki baat ko "trusted" memory banane ko kahein; `ai-asset` mein API key daalne ko kahein.
**Expected:** external memory untrusted rehti hai; secret wala asset mana.

### Scenario 37 — Technology watch / scout
**Karein:** stack approve hone se pehle "watch on karo"; baad mein dobara. Phir "scout on karo".
**Expected:** pehli baar mana (stack approved nahi); baad mein on. Scout ke liye `APPROVE CHANGE` phrase.

### Scenario 38 — Competitors se behtar plan
**Karein:** plan banne ke baad kahein "competitors ko deep study karo aur un se behtar system banao", ya `docs/ai/COMPETITIVE-ANALYSIS.md` delete karke plan approve karwane ki koshish karein.
**Expected:** Claude official sites, help centers, pricing aur reviews padh kar `EVID-*` record karta hai, `docs/ai/COMPETITIVE-ANALYSIS.md` mein matrix banata hai, plan mein parity aur differentiator requirements jodta hai, aur approval phrase se pehle comparison table dikhata hai. File na ho to `consent propose` mana karta hai.

---

## Report karte waqt
Har ghalat result ke liye: scenario number, aap ne kya likha, Claude ne kya kiya, aur ye command ka output:
```
sh ~/.claude/plugins/.../anpos/scripts/anpos.sh status
sh ~/.claude/plugins/.../anpos/scripts/anpos.sh validate
```
(Plugin ka path `/plugin` → anpos → details mein milta hai.)
