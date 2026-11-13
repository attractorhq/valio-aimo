# Team workflow: 3 developer machines + 1 architect session + blind judge

```
            GitHub Issues (label: ticket)  ── the work queue
                 │  scripts/ticket next / claim   (atomic: creates branch ticket/N)
   ┌─────────────┼──────────────┬──────────────┐
 dev agent A  dev agent B   dev agent C   dev agent D      (skill: developer, or Codex via AGENTS.md)
   │ worktree .worktrees/ticket-N, commits "TNN: …", tests, scripts/check
   └── scripts/ticket pr N ──► Pull request (Closes #N, status:review)
                                   │
                         architect session (skill: architect)
                         review → merge (squash) → ticket refresh → deploy → smoke test
                                   │                                  │
                                   │                       scripts/judge <url> (blind, isolated)
                                   │                                  │
                                   └──── new tickets (label: judge) ◄─┘ judging/reports/<ts>.md
                                   ▼
                https://valio-aimo.<account>.workers.dev   (Worker + Container, one origin)
```

## Decisions

| Topic | Decision | Date |
|---|---|---|
| Stack | Python 3.12 FastAPI (domain + API + agent) · React/Vite UI · TypeScript Worker only as the edge | 13 Nov |
| Team | 1 architect machine (this one: Docker, wrangler login, `data/`, fixtures, production secrets) + 3 developer machines | 13 Nov |
| Hosting | Cloudflare Worker + Cloudflare Container (Workers Paid, account Attractorfi), one origin | 13 Nov |
| LLM | **GLM via OpenRouter** (OpenAI-compatible API, `https://openrouter.ai/api/v1`). Key `OPENROUTER_API_KEY` (Worker secret), model id in `LLM_MODEL`. Deterministic fallback always on | 13 Nov |
| Persistence of orders/audit across container restarts | **None: accepted reset.** State lives in the container's SQLite and resets on restart/deploy. Documented in the README; production path = a durable database | 13 Nov |
| Priorities | P0 complete and polished before any P1/P2 (patch P6, AC18); enforced by `scripts/ticket`, override label `unlocked` | 13 Nov |
| Test data | Committed tests are synthetic; recorded API responses live in `data/fixtures/` (gitignored, P7), shared privately | 13 Nov |
| Submission deadline | **open**: check the Junction dashboard | |

## Running agents in a loop

| Agent | Command | Behaviour |
|---|---|---|
| Claude Code developer | `/loop /developer` | Self-paced. One ticket per iteration; fixes review feedback first; waits when nothing is claimable. |
| Codex developer | `scripts/agent-loop codex` | A fresh `codex exec` per ticket (clean context). Sleeps 5 min when the queue is empty. |
| Architect (Claude Code) | `/loop 10m /architect` | Reviews, merges and deploys every 10 minutes, and judges when due. |
| Blind judge | `scripts/judge <url>` (architect only) | 3 isolated personas in parallel → `judging/reports/` |

## Who touches what

- **Cloudflare**, the **judge** and the **production secrets** belong to the architect machine only.
- **Developers** work locally and put remote steps under **"Architect runs:"** in the PR.
- `scripts/check` is the gate on every machine. CI runs it too.

## Deploy (architect machine only)

One origin, `https://valio-aimo.<account>.workers.dev`:

- **`edge/`** is the Worker. It serves `frontend/dist` with SPA fallback and forwards `/v0.1/*`, `/mcp`, `/health`, `/docs` and `/openapi.json` to one container (`standard-1`, `max_instances: 1`, `sleepAfter` 6 h).
  - The agent channel `/v0.1/agent/*` is rate limited to 30 requests per minute per client IP.
  - The path list lives in two places: `edge/src/routes.ts` and `assets.run_worker_first` in `edge/wrangler.jsonc`. A test keeps them in sync.
- **`backend/Dockerfile`** is the container image, built from the repo root.
  - It copies `data/catalog/` and `data/fixtures/` (gitignored, never committed) in at build time.
  - `data/raw` stays out (`.dockerignore`).
  - State resets on every restart and deploy (see Decisions).
- **Secrets** are Worker secrets, passed to the container as env vars by name: `VALIO_API_KEY`, `OPENROUTER_API_KEY`, `APPROVAL_SIGNING_KEY`. Set them once:
  ```bash
  cd edge && npx wrangler secret put VALIO_API_KEY      # repeat for OPENROUTER_API_KEY, APPROVAL_SIGNING_KEY
  ```
  Non-secret settings (`MOCK_MODE`, `LLM_MODEL`, `VALIO_BASE_URL`) are `vars` in `edge/wrangler.jsonc`.
- **Deploy:** `scripts/deploy` builds the catalogue, then the UI, then runs `wrangler deploy`, which builds and pushes the image with Docker.
  - `scripts/deploy --containers-rollout=none` updates the Worker and UI only.
- **Locally:** `docker compose up --build` runs the same image. `cd edge && npm run check` typechecks and tests the Worker.

## Getting started (each person)

```bash
gh repo clone attractorhq/valio-aimo && cd valio-aimo
cp .env.example .env              # VALIO_API_KEY etc.: ask in the team chat, never commit
# The sanitised brief + patch are in git (docs/brief/). Copy the recorded fixtures to data/fixtures/ (ask in chat),
# and download the challenge files to data/raw/:
scripts/worktree-setup
.venv/bin/python scripts/download_valio_data.py      # products.json + 2 CSVs via the API key (skips existing files)
claude                            # then: /developer   (or /architect on the architect machine)
```

## Why two agents can never take the same ticket

`scripts/ticket claim N` creates the git ref `refs/heads/ticket/N` through the GitHub REST API. Ref creation is atomic on GitHub's side: the second request gets `422 Reference already exists`, and the script stops with "already claimed". Labels and assignees are updated only after the ref exists, so they are informative, not the lock.

## Ticket anatomy

```
**Ticket:** T04 · **Lane:** core · **Priority:** P0
Depends on: #2
Touches: backend/app/catalog/search.py, …

## Goal
## Scope
## Acceptance
```

Labels:
- `ticket`;
- `P0` | `P1` | `P2`;
- `unlocked` (the architect lets a P1/P2 ticket start before every P0 is closed);
- `lane:data|core|agent|api|web|infra|demo`;
- `status:ready|blocked|in-progress|review`;
- `contract-change` (on PRs);
- `judge` (ticket created from a blind-judge finding).

## Rules of thumb

- One ticket = one PR, small and reviewable. Commit prefix `TNN:`.
- Everything is written in English.
- Contracts change only with a `contract-change` PR label.
- UI work never waits for the backend: use `frontend/src/mocks/*.json` that match `docs/API.md`.
- Domain work never waits for the network: use synthetic records in `backend/tests/synthetic/` (committed) and recorded responses in `data/fixtures/` (gitignored).
- No data, fixtures, unsanitised brief or secrets in git. Ever. (The sanitised brief in `docs/brief/` is the deliberate exception; remove it before any public release.)
- The judge's view is the jury's view. Fix what it saw, never how it judges.
