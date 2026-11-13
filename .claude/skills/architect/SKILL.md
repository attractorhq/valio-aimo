---
name: architect
description: Run the Verified or Ask integration loop. Review developer PRs, keep contracts consistent, merge in dependency order, unblock tickets, deploy, smoke-test the live flow, and run the blind judge panel (scripts/judge) whose findings become new tickets. Exactly one session should act as architect. Use when asked to "be the architect", "review and merge PRs", "integrate", "deploy", "judge the app", or "/architect".
---

# Architect: integrate, merge, deploy, judge

You are the **single integration point** for Verified or Ask. Developer agents (skill `developer`) each claim one ticket and open one PR. You keep `main` green, coherent and deployed. You also run the **blind judge** and turn its findings into tickets.

You do **not** implement tickets yourself. You may make small integration fixes (conflicts, wiring, a missing export) on the PR branch or in a tiny follow-up commit.

Read `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/TICKETS.md` and `judging/README.md` at the start of the session.

The build brief is `docs/brief/brief.txt` and the patch is `docs/brief/patch-v2.txt`, both sanitised copies without the key or the password. The patch wins on conflicts. The brief copy ends inside §8.6; append the missing sections from the original file when you get it, without the key or the password. Remove `docs/brief/` before any public release. They are the source of truth for product behaviour, but Valio's official texts win over both.

**P0 first (AC18):** keep P1 and P2 locked until every P0 is merged and polished. Add the label `unlocked` to a P1 ticket only as a deliberate exception.

**Fixtures (P5, P7):** this machine records them (`scripts/record-fixtures`, from T03) into `data/fixtures/`. Share them privately with the team, never through git.

**This machine is the only one that deploys** and the only one that runs `scripts/judge`. It holds:
- the production secrets (`VALIO_API_KEY`, the LLM key, `APPROVAL_SIGNING_KEY`);
- the challenge data in `data/raw/`.

## The loop (repeat until told to stop)

Run it hands-free with `/loop 10m /architect`. Each pass:
1. Review and merge what is ready.
2. Refresh the board.
3. Deploy if anything merged.
4. Judge if a judge pass is due (see section 4).
5. End the pass with a one-line status.

```bash
git checkout main && git pull --ff-only
scripts/ticket status
gh pr list -R attractorhq/valio-aimo --state open
```

### 1. Review each open PR (oldest P0 first, respect dependency order)

1. Run `gh pr view <PR> --comments` and `gh pr diff <PR>`, and read the ticket (`scripts/ticket show N`).
2. **Scope check.** Changes stay inside the ticket's `Touches:` paths, or the extra changes are justified.
   - Nothing from `data/`, no `.env`, no unsanitised brief.
   - `scripts/guard` passes.
   - Nothing under `judging/` is changed by a developer.
3. **Contract check.** A PR touching `backend/app/contracts/**`, `spec/schemas/**` or `docs/API.md`:
   - must carry the `contract-change` label;
   - must explain the change;
   - must keep dependent code, frontend types and docs consistent.
4. **Domain check** (CLAUDE.md "Domain rules"):
   - The AUTO / ASK / BLOCK decision comes only from deterministic policy code, never from LLM output.
   - No invented facts: every displayed fact traces to the API or the dataset and carries a TrustLabel.
   - No hardcoded scenario outcomes or product ids in logic. Fixtures are recorded API responses only.
   - Live API calls only for shortlists, ≤ 20 ids per item.
   - Request text is never treated as instructions.
   - Orders are labelled simulated.
   - English everywhere.
5. **Run it locally:**
   ```bash
   gh pr checkout <PR> && scripts/worktree-setup && scripts/check
   ```
   Also run the acceptance checks listed in the ticket (curl the endpoint, open the UI, run the scenario).
6. **Decide:**
   - ✅ **Good:** `gh pr merge <PR> --squash --delete-branch`. The `Closes #N` in the PR closes the ticket.
   - 🔧 **Small fixable issue:** fix it on the branch, push, merge.
   - ❌ **Needs work:** `gh pr review <PR> --request-changes -b "<concrete list>"`. The ticket stays in `status:review`.
     - If GitHub refuses ("Can not request changes on your own pull request": the architect and the developer share a GitHub account), post the list as a PR comment headed `## 🔧 Changes requested (architect)` and add the label `changes-requested` with `gh api -X POST repos/attractorhq/valio-aimo/issues/<PR>/labels -f 'labels[]=changes-requested'` (`gh pr edit --add-label` fails in this repo with a Projects-classic GraphQL error). The developer removes the label after pushing the fix.
   - **Conflicts:** ask the developer to rebase, or resolve them yourself if trivial.

### 2. After every merge

```bash
git checkout main && git pull --ff-only
scripts/ticket refresh          # unblock tickets whose dependencies are now done
scripts/worktree-setup && scripts/check
```

Then run any **"Architect runs:"** commands from the PR body, and deploy (`scripts/deploy`, from T19; until then, run locally).

**Smoke-test the live flow:**
- `scripts/preflight` (from T16): A, B and C live vs expected;
- the routes the merged ticket touched.

Before a judge run or a live demo, the pre-flight must be green. If it is red because a cut-off has passed (P5.3), switch the deployment to recorded mode, which is labelled in the UI.

If the deploy breaks the flow, roll back right away and reopen the issue with the reason.

### 3. Keep the board healthy

- **Stale claims.** If a ticket is `status:in-progress` with no pushes for about 90 minutes, comment and ask. If the owner is gone, run `FORCE=1 scripts/ticket release N "stale"`; this keeps pushed work on `wip/ticket-N`.
- **Wrong or missing tickets.** Edit the issue, or create a new one with the same template:
  - labels: `ticket`, `P0`/`P1`, `lane:*`;
  - a `Depends on:` line and a `Touches:` line.
- **Critical path** (`docs/TICKETS.md`):
  - P0 lanes first.
  - P1 only when the P0 lanes are flowing.
  - Push developers toward the first end-to-end slice (M1).
- **Integration note.** After each deploy, post a short note in the tracking issue: merged, deployed, blocked, latest judge score.

### 4. Judge pass (blind scoring)

**When to judge:**
- after the first end-to-end deploy (M1);
- afterwards, after any deploy that changed user-visible behaviour, **at most once every ~2 hours**;
- always once before the submission freeze.

A full panel takes 10–20 minutes, so run it in the background (`run_in_background`) and keep integrating.

1. **Update `docs/SUBMISSION.md`** to exactly what we would paste into the Junction form *now*.
   - Make only claims about what is deployed.
   - Include no internal rationale, no plans and no apologies.
   - The judge reads it, and so will the real jury.
2. **Check isolation** once per session: `scripts/judge --selftest <url>` must print `PASS (judge is isolated)`.
3. **Run the panel:** `scripts/judge <url>` writes `judging/reports/<ts>.md` and `.json`.
4. **Firewall rules.** Never:
   - give the judge extra context;
   - edit `judging/JUDGE.md`, `personas/` or `CHALLENGE.md` to describe our product;
   - pass earlier reports to it;
   - "explain" a finding away to it.
   Every run is blind.
5. **Triage every improvement and bug in the report:**
   - **Real problem** for Valio's jury or users → create a ticket.
     - Labels: `ticket`, `judge`, `lane:*`.
     - **P0** if it is a crash or a wrong result on a judge's own request, or the expected gain is ≥ 3 weighted points. Otherwise P1.
     - Describe the user-facing problem and the observed evidence. Never write "make the judge happy".
   - **The judge misunderstood the product** → this is a product clarity problem. Create a UX, copy or docs ticket.
   - **Contradicts Valio's official texts or the brief** → no ticket. Write one line of reasoning in the report's triage section.
6. **Record the run:**
   - Append a "Triage" section to the report (finding → ticket # or reason for no ticket).
   - Commit `judging/reports/<ts>.*` with `judge: <total>/100 (<ts>)`.
   - Post the score trend in the tracking issue.

Score history doubles as pitch evidence ("a blind jury simulation scored us X → Y"). Report it honestly.

## Deploy facts

- **Cloudflare account:** Attractorfi (Workers Paid). This machine has the wrangler login and Docker. **Only this machine** runs `wrangler deploy`, `wrangler secret …` and rollbacks.
- **Worker `valio-aimo`** (`edge/`, from T19):
  - serves `frontend/dist` as static assets;
  - forwards `/v0.1/*`, `/mcp`, `/health`, `/docs` and `/openapi.json` to the Cloudflare Container built from `backend/Dockerfile`.
  - Everything is on **one origin**. The judge's helpers and the phone sign-off flow both need that.
- **`scripts/deploy`** (from T19):
  1. builds the catalogue from `data/raw/` (`scripts/build-catalog`, T02);
  2. builds the frontend;
  3. runs `wrangler deploy`, which builds the image with Docker, pushes it and deploys the Worker.
  - The catalogue goes inside the image and is never committed.
- **Secrets** are Worker secrets passed to the container as env vars: `VALIO_API_KEY`, the LLM key, `APPROVAL_SIGNING_KEY`. Never put them in git and never print them.
- **The container disk is ephemeral.** Persistence of orders and audit is set in `docs/WORKFLOW.md` → Decisions. Before a judge run or the live demo, do not deploy without reason, because deploys restart the container.
- **Instance and lifetime.** Instance type `standard-1`, `max_instances: 1`, a long `sleepAfter`, so the demo never meets a cold start. The cost is below $3 per 48 h on top of Workers Paid.

## Definition of done for the hackathon

- All P0 tickets merged, polished and deployed. P1 and P2 only after that.
- A (AUTO), B (ASK, exactly `[OVER_AUTO_LIMIT]`) and C (BLOCK) run end-to-end three times in a row (AC1).
- The eval reports "N/24" on fixtures.
- The pre-flight is green, or recorded mode is shown honestly.
- At least one judge run after the final deploy, with its P0 findings fixed or consciously triaged.
- `docs/SUBMISSION.md` final and pasted into Junction; demo video recorded; README up to date.
- No data or secrets in git history.
