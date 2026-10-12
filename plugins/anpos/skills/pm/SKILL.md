---
name: pm
description: Choose, connect and sync a project management tool (Linear, GitHub Projects/Issues, Jira, ClickUp, Asana, Notion, …) for an ANPOS project, mirroring the approved plan and reading back status. Use when the user wants their ANPOS plan in a PM tool, asks to sync or check PM status, or during ANPOS setup ("Choose Project Management System").
---

# ANPOS — Project Management

CLI: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/anpos.sh"` (`anpos`). Reply in the user's language.

The repository stays the source of truth for code, PRs, tests, releases and work-unit completion (`config/integrations/sync-authority.json`). The PM tool is a mirror for planning and progress. Optional: skipping is always fine.

## 1. Choose Project Management System
1. `anpos pm providers` lists the catalog. Linear is recommended, not required.
2. Only offer a provider as connectable when a real path exists **in this session**:
   - **GitHub Projects / Issues** — `gh` installed and logged in (the CLI reports `ready`).
   - **Any other tool** — a connector/MCP server for it is present in this session (look for its tools, e.g. `mcp__linear__…`, `mcp__atlassian__…`). If none is connected, say which connector would help and offer CSV export (`anpos export --format csv`) or GitHub instead.
   - Never ask for passwords, API tokens or cookies in chat.
3. Map the workspace/project with the connector (list the user's workspaces/projects, let them pick or create one when authorized), then record it:
   - GitHub: `anpos pm select github_projects [--project-url <GitHub Projects board URL>]`
   - Others: `anpos pm select <id> --workspace-id … --workspace-name … --project-id … --project-name … --project-url … --verified` (`--verified` only after you read the project back through the connector).
   - No tool: `anpos pm skip`.

## 2. Push the approved plan (sync on plan approval, plan revision, and work-unit changes)
- `anpos pm plan` shows create/update/archive actions. Only an approved plan is pushed.
- **GitHub:** `anpos pm apply` creates one milestone per phase and one issue per work unit (label `anpos`), updates changed items, closes issues of completed work units, and finds existing items by their idempotency marker instead of duplicating. Ask before the first push.
- **Connector tools:** `anpos pm plan --json` gives each action's title, body (keep the `<!-- anpos:… -->` marker in the body), state and parent phase. Apply each action with the connector (phase → milestone/epic/cycle, work unit → issue/task), then record it: `anpos pm record <ID> --external-id <provider id> --url <link> --revision <updated-at if available>`. Before creating, search the provider for the marker so a retry never duplicates.

## 3. Read back status
- GitHub: `anpos pm pull`. Others: read item states with the connector, write `[{"external_id": "…", "state": "…", "revision": "…"}]` to a temp file, then `anpos pm pull --from-json <file>`.
- A close/reopen in the PM tool that disagrees with the repository becomes a **conflict** — it never changes a work unit. Show `anpos pm conflicts` to the user; fix the real state in the repo (finish the work with evidence, or reopen the PM item) and close the conflict with `anpos pm conflicts --resolve <ID> --resolution "<what was decided>"`.

## 4. Switching tools
Pull first, then `anpos pm select <new> --switch` (old external ids are kept in history), then push again.

Show `anpos pm status` when the user asks how the sync is doing.
