# AGENTS.md: instructions for Codex (and any non-Claude agent)

This repo is built by several agents in parallel. Claude Code users run the `developer` / `architect` skills. **Codex follows the same procedure by reading the files below.**

1. Read `CLAUDE.md`: project rules, domain rules (trust labels, LLM never decides, dataset traps), conventions.
2. You are a **developer**. Follow `.claude/skills/developer/SKILL.md` step by step:
   - `scripts/ticket next` → `scripts/ticket claim <N>`. The claim is atomic; if it fails, pick another ticket.
   - Work only in a worktree on branch `ticket/<N>`: `git worktree add .worktrees/ticket-<N> ticket/<N>`.
   - Implement only the ticket's scope (`Touches:`) and add tests.
   - `scripts/check` must pass.
   - `git push origin ticket/<N>` → `scripts/ticket pr <N> <body-file>`.
3. Never merge and never deploy; the architect session does that. Never commit:
   - data: `data/`, `*.db`, the build brief;
   - secrets: `.env`, API keys, the portal password.
4. **Never run the judge** (`scripts/judge`) and never edit `judging/`. Only the architect does.
   **Never touch Cloudflare:** no `wrangler deploy`, no `wrangler secret`, no rollbacks. Work locally (uvicorn + vite, or `docker compose up`). Put any remote steps into the PR body under "Architect runs:".
5. You need network access for `gh` (GitHub API), `pip` and `npm`. `scripts/check` is the local gate.
6. **Write in English:** UI text, code comments, docs, commit messages, PR bodies, issues and comments.

Loop: `scripts/agent-loop codex` runs one fresh Codex process per ticket until the queue is empty.

Docs: `docs/PLAN.md`, `docs/TICKETS.md`, `docs/WORKFLOW.md`, `docs/API.md` (from T01).
