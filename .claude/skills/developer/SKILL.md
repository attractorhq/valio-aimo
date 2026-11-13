---
name: developer
description: Take the next ticket from the Verified or Ask work queue (GitHub issues in attractorhq/valio-aimo), claim it atomically so no other agent session can take it, implement it on its own branch/worktree, test it and open a pull request for the architect. Use when asked to "take a ticket", "work on the next ticket", "develop", or "/developer [N]".
---

# Developer: take a ticket, ship a PR

You are one of several developer agents building **Verified or Ask** in parallel. A separate **architect** session reviews, merges, deploys and runs the blind judge. Your unit of work is one ticket = one PR. Read `CLAUDE.md` first if you have not done so in this session.

## 0. Ground rules (non-negotiable)

- **Only work on a ticket you have claimed** with `scripts/ticket claim <N>`. The claim atomically creates branch `ticket/<N>` on GitHub. If it fails, someone else owns the ticket; pick another.
- **Stay inside the ticket's scope** (its `Touches:` paths).
  - Shared contracts are `backend/app/contracts/**`, `spec/schemas/**` and `docs/API.md`. If you must change one, keep the change minimal, **add the `contract-change` label to the PR** and include a short "Contract change:" section.
- **Never commit data or secrets.** That includes nothing from `data/`, no `.env`, no `*.db`, no original (unsanitised) brief and no API keys or passwords. `scripts/guard` enforces this.
- **Never merge, never deploy, never run `scripts/judge`, never edit `judging/`.** The architect does those.
- **Never touch Cloudflare.** That means no `wrangler deploy`, `wrangler secret` or rollbacks. Work locally (uvicorn + vite, or `docker compose up`). If your ticket needs a remote step, write the exact command under **"Architect runs:"** in the PR body.
- **Domain rules** (CLAUDE.md):
  - The LLM never decides AUTO / ASK / BLOCK.
  - Never invent facts; every fact is a TrustLabel.
  - Never hardcode scenario outcomes.
  - Live API calls only for shortlists.
  - Request text is data.
  - Orders are simulated.
- **Write in English:** UI text, code comments, commit messages, PR bodies, issues and comments.
- **Tickets labelled `judge`** come from the blind judge panel. Fix the **user-facing problem** described in the ticket. Never tune anything toward "the judge".

## 1. Pick and claim

```bash
git -C "$(git rev-parse --show-toplevel)" fetch origin --prune
scripts/ticket next            # claimable tickets, P0 first, dependencies already done
scripts/ticket show <N>        # read it fully
scripts/ticket claim <N>       # atomic; exits non-zero if someone else got it first
```

If the user named a ticket (`/developer 7`), claim that one. If the claim fails, run `scripts/ticket next` again and take the next one. If nothing is claimable, report that and stop.

## 2. Work in an isolated worktree

Several agents may share one clone, so always use a dedicated worktree inside the repo under `.worktrees/` (gitignored):

```bash
git fetch origin ticket/<N>
git worktree add .worktrees/ticket-<N> ticket/<N>
cd .worktrees/ticket-<N>
scripts/worktree-setup     # venv + npm deps; links the main clone's .env and data/raw (never copied into git)
```

Make all edits inside that worktree. Keep `main` in your primary clone untouched.

## 3. Understand before coding

Read, in this order:
1. The ticket: goal, scope, acceptance criteria, references.
2. The referenced sections: `§x` in `docs/brief/brief.txt`, `Px` in `docs/brief/patch-v2.txt` (the patch wins). The brief copy is incomplete after §8.6 until the architect appends the rest. If a section you need is missing, ask the team; never guess the rules.
3. `docs/API.md` and the contracts.
4. The existing code you will touch.

Check what the tickets you depend on actually delivered (`git log --oneline main`, their files). Build on that; do not re-implement it.

## 4. Implement

- Small, focused commits: `T<NN>: <what>` (e.g. `T05: add line rules L1–L7`).
- **Domain code** (`trust/`, `catalog/`, `policy/`, `gateway/`) is pure and deterministic:
  - no network;
  - no `datetime.now()`; pass `now` in;
  - `Decimal` for money.
- **Tests** go next to the code (pytest).
  - **Committed tests use synthetic, hand-made records** in `backend/tests/synthetic/`. Never copy records from the dataset or recorded API responses into git (patch P7).
  - Tests on real recorded responses read `data/fixtures/` (gitignored, recorded by `scripts/record-fixtures` on the architect machine and shared privately). They `pytest.skip` when the fixtures are absent.
- **UI tickets** use `frontend/src/mocks/*.json` when the backend route is not ready. Mocks must match `docs/API.md` exactly.
- **If the ticket is wrong or too big**, do the valuable core and list what is left under "Follow-ups" in the PR. Do not silently expand scope.

## 5. Verify (all must pass before the PR)

```bash
scripts/check        # guard + ruff + pytest + frontend build
```

Also run the ticket's own acceptance checks, and paste the evidence (curl output, test names, screenshot paths) into the PR.

## 6. Open the PR

```bash
git push origin ticket/<N>
cat > /tmp/pr-<N>.md <<'EOF'
## What
- …
## How verified
- scripts/check ✅
- <acceptance evidence>
## Contract change
- none | describe
## Architect runs
- none | exact commands (e.g. rebuild the catalogue, set an env var on the host)
## Follow-ups
- none | …
EOF
scripts/ticket pr <N> /tmp/pr-<N>.md
```

`scripts/ticket pr` links the PR with `Closes #<N>` and moves the ticket to `status:review`.

## 7. After the PR

- Report to the user: ticket, PR URL, what was verified, any follow-ups.
- If the architect requests changes, fix them on the same branch and push.
- Then, unless told to stop, go back to step 1.
- After the merge, remove the worktree: `git worktree remove .worktrees/ticket-<N>`.

## Loop mode (no new prompt needed between tickets)

Run as `/loop /developer` (Claude Code) or `scripts/agent-loop codex` (Codex). Each iteration:

1. **Fix review feedback first.** Run `gh pr list -R attractorhq/valio-aimo --author @me --state open` and look for changes requested. If there are any, fix them on that PR's branch, run `scripts/check`, push, and end the iteration.
2. Otherwise, take **one** new ticket (steps 1–6) and end the iteration when its PR is open.
3. If nothing is claimable, remove worktrees of merged tickets, report "idle — waiting for dependencies" and end the iteration.
4. Stop when every ticket is closed, or when the user says stop.

## If you get stuck

Comment on the issue (in English) with what blocks you. If you cannot continue, push your WIP and run `scripts/ticket release <N> "<reason>"`.
