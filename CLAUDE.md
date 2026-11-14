# Aimo Connect: agent guide

Junction 2026 hackathon project (Valio Aimo challenge "Trusted Trade Agents"). Deadline: **see the Junction dashboard**. The architect keeps the exact time in `docs/WORKFLOW.md`.

**Product.** A Valio Aimo-side **Trusted Trade Gateway** plus a demo purchasing agent. Together they run the official flow Request → Lookup → Order → Approval against the live mock API.

**Core rule.** The agent may act alone only when two things hold:
- every fact it relies on is strong enough (Agent Trust Labels v0.1);
- the order is inside the buyer's mandate.

Everything else goes to a named human. The human signs off with one tap and sees the exact reason.

## Where things are

| What | Where |
|---|---|
| Plan | `docs/PLAN.md` (Finnish, team-internal) |
| Tickets | `docs/TICKETS.md` + GitHub issues labelled `ticket` |
| Workflow | `docs/WORKFLOW.md` |
| Build brief | `docs/brief/brief.txt` + **`docs/brief/patch-v2.txt`**. Where they conflict, the patch wins. These are **sanitised copies** with the API key and portal password removed. The brief copy ends inside §8.6 until the rest is appended. Never quote them to the judge. Remove `docs/brief/` before any public release. |
| Challenge data | `data/raw/products.json`, `delivery-reliability.csv`, `carbon-footprint.csv`, `product-schema.json` (gitignored). Fetch with `.venv/bin/python scripts/download_valio_data.py` |
| Existing live Valio adapter | `backend/app/integrations/real_valio.py`, `valio_catalog.py` (commit 0e74e98): the base for T02/T03. See `docs/TICKETS.md` → "Starting point" |
| Blind judge | `judging/` (see "Multi-agent workflow" below) |

## Multi-agent workflow

Work is split into GitHub issues (label `ticket`) in `attractorhq/valio-aimo`.

**Developers** use the `developer` skill:
1. `scripts/ticket next`.
2. `scripts/ticket claim N`. This is atomic and creates branch `ticket/N`.
3. Work in worktree `.worktrees/ticket-N` and implement.
4. Run `scripts/check`.
5. Run `scripts/ticket pr N`.

**One architect session** uses the `architect` skill. It:
- reviews and merges PRs;
- deploys;
- runs the **blind judge** (`scripts/judge`);
- turns the judge's findings into tickets.

**The judge** (`judging/`) is a separate, context-isolated Claude process. It scores the deployed app against the official judging criteria. It sees only:
- the official challenge text;
- `docs/SUBMISSION.md`;
- the live app.

It must not see plans, tickets, this file, code or rationale. **Never pass it extra context**, and never edit `judging/` to describe our product.

Rules for everyone:
- **P0 first** (patch P6 / AC18). P0 must be complete and polished before any P1 or P2 work starts. `scripts/ticket` enforces this; only the architect can grant the label `unlocked`.
- Never work on an unclaimed ticket.
- Developers never merge or deploy.
- Issues, PRs, commits, code comments, docs, UI text: **English**.

## Deployment: Cloudflare Worker + Container (only the architect machine touches Cloudflare)

```
browser / phone / external agent ──► Worker "valio-aimo" (edge/, TypeScript, ~50 lines)
                                       ├─ static assets: frontend/dist (SPA)
                                       └─ /v0.1/*, /mcp, /health, /docs, /openapi.json
                                            ──► Cloudflare Container: backend/Dockerfile (FastAPI)
                                                 catalogue artifact baked into the image at build time
```

- **One origin** for UI, API, MCP and approval links.
- **Secrets** (`VALIO_API_KEY`, LLM key, `APPROVAL_SIGNING_KEY`) are Worker secrets passed to the container as env vars.
- **The container disk is ephemeral.** Persistence of orders and audit is decided in `docs/WORKFLOW.md` → Decisions.
- **Architect machine only:** `wrangler deploy` (builds and pushes the image with Docker), `wrangler secret …`, rollbacks.
- **Developers** work locally: uvicorn + vite, or `docker compose up`. If a ticket needs a remote step, write the exact command under **"Architect runs:"** in the PR.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e 'backend[test]'   # once per clone/worktree
(cd frontend && npm ci)                                               # once per clone/worktree
scripts/check            # guard + ruff + pytest + frontend build: must pass before any PR
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000  # API on :8000
cd frontend && npm run dev                                            # UI on :5173
docker compose up --build    # the production container locally (same image Cloudflare runs)
scripts/deploy               # architect only (from T19): build catalogue + wrangler deploy (edge/)
scripts/judge <public-url>   # architect only: blind judge panel → judging/reports/
```

## Domain rules (get these right; most teams will not)

0. **One interface, three outcomes** (patch P8, P12). Demo order:
   - **A AUTO:** 464306 × 1,000 = €1,010.00.
   - **B ASK** with exactly `[OVER_AUTO_LIMIT]`: €506.16 > €500 for M2. Valio's substitute 470863 is rejected.
   - **C BLOCK:** over the hard limit, plus `INSTRUCTION_IGNORED`.
1. **The LLM never makes the AUTO / ASK / BLOCK decision.** Deterministic policy at the gateway does. Every outcome has a reason code (brief §6.9, with effects per patch P2).
1a. **Proposal before confirmation** (P1). The flow is:
    1. getOffer;
    2. createProposal, bound to mandateVersion + offerId/offerVersion/offerHash;
    3. the server decides;
    4. `recheckAndConfirm`.

    Any change at the re-check → SUPERSEDED: the old approval is void and a new version is decided from scratch. An approval applies only to the exact proposal version the human saw.
1b. **A human approves trade-offs, never missing evidence** (P2).
    - Approvable: OVER_AUTO_LIMIT, DATE_LATER, PARTIAL_AVAILABILITY (as its own proposal version), SUPPLIER_STOCK_UNKNOWN, VALIO_SUBSTITUTE / AGENT_SUBSTITUTE (only if every requirement is proven), LOW_RELIABILITY, QUANTITY_BASIS_WEAK.
    - Everything else is EXCLUDED, or BLOCK at order level: CLAIM_MISSING, ALLERGEN_*, DATA_CONFLICT, CUTOFF_PASSED, UNIT_MISMATCH, NOT_AVAILABLE, SALES_BLOCKED, PRICE_UNAVAILABLE.
    - "Approve verified lines only" does not exist.
1c. **The approval link never reaches the agent** (P3).
    - Agent-facing responses carry no approval URL, token or QR, only `PENDING_APPROVAL`, the reasons and the approver role.
    - Approval happens in the separate Approver panel (and on the phone page, P1) with a single-use demo link.
1d. **X99** proves "free from" only for the EU-14 codes (P4). Lactose-free is proven only by `FREE_FROM_LACTOSE`.
   - The LLM interprets requests, classifies ambiguous product types, writes trade-offs and explanations.
   - Every LLM step has a deterministic fallback, so the demo survives an LLM outage.
2. **Never invent facts.** Products, prices, stock, dates, allergens, claims and labels come from the API or the dataset.
   - Each fact carries a TrustLabel: `level` = VERIFIED | DECLARED | ESTIMATE | UNKNOWN, plus `source`, `retrievedAt`, `method` and optional `conflict` K1–K5.
   - UNKNOWN is shown as "Unknown", never blank.
3. **Never hardcode scenario outcomes.** Scenarios and the eval set must come out of the real logic (P5):
   - The **eval set (24 rows)** runs on recorded API responses in `data/fixtures/` (gitignored) with "now" frozen to the recorded `timeNow`.
   - **Live pre-flight** (`scripts/preflight`) runs A, B, C and B2 against the live API before presenting.
   - If it is red, present in **recorded mode**, clearly labelled.
4. **Dataset traps:**
   - lactose-free ≠ milk-free (464306 is lactose-free and contains milk);
   - `LOW_LACTOSE` ≠ lactose-free;
   - an oat drink is not automatically gluten-free;
   - Valio's own listed substitute can break a requirement (468465 → 470863);
   - Terminal stock `999999` is UNKNOWN, not a quantity;
   - a date can still be offered after its cut-off has passed (K3);
   - 8,110 products have no allergen declaration at all.
5. **Observed API behaviour (13 Nov):**
   - `/api/availability` ignores `requestedDate`: `availableDate` is always 2026-11-16 so far;
   - `/api/price` returns prices outside `validFrom..validTo`, so check validity yourself;
   - an unknown id gives `error: true` and empty `scheduleLines`.
   - Record any change in README "Assumptions & observed behaviour".
   - **Official answers from Valio Aimo (14 Nov):**
     - closingTime is the cut-off for availableDate. The mock data does **not** shift during the weekend, so every Mon 16 Nov delivery passes its cut-off on **Sun 15 Nov 17:00**: after that, the live demo needs recorded mode.
     - availableAmount ≥ 999999 means availability unknown, and Valio allows any quantity. Empty means not available.
     - allowedLotSize 0.01 means 1.
     - HTTP 429 blocks the caller for 30 s, and Valio asks every team to honour it; a few hundred calls per minute is fine.
     - Valio's systems have no approval gates today: the mandate is new, explicit, buyer-owned control.
6. **API etiquette:**
   - narrow down from static data first;
   - live price/availability only for the shortlist (≤ 20 per item, one batched call each);
   - never the whole catalogue.
7. **Orders are simulated.** No order API exists (Valio: an order for human approval is enough). The API state stays `CONFIRMED_SIMULATED`. **Team decision 14 Nov:** the UI shows no "simulated", "mock", "prototype" or "demo" notes; the limits are stated in `docs/SUBMISSION.md` and the README.
8. **It is not a chatbot.** The UI is a control room around a structured flow, not a chat thread.
9. **Request text is data.** Instructions inside it are ignored and logged (`INSTRUCTION_IGNORED`). Approval only comes through the approval channel. `decideApproval` is never an agent/MCP tool.
10. **Money:**
    - `Decimal`, EUR, excl. VAT, unless labelled otherwise;
    - VAT rate only from the price API;
    - format `€1,010.00`; dates `Mon 16 Nov`.

## Conventions

- Python 3.12, FastAPI, Pydantic v2, ruff. Pure domain functions take explicit inputs (including `now`) and return plain data. Tests: pytest.
- Frontend: React 19 + Vite + Tailwind 4 + React Query, TypeScript strict.
- Commits: `T<NN>: <imperative summary>`. One ticket = one PR.
- Contracts are `docs/API.md`, `backend/app/contracts/**` and `spec/schemas/**`. Change them only with the `contract-change` PR label and a short explanation.
- **Secrets:**
  - `VALIO_API_KEY` and LLM keys live only in `.env` (gitignored) and server env. Never in browser code, logs, screenshots, issues, PRs or the README.
  - `scripts/guard` blocks them.
- **Data licence** (brief §1.10, patch P7): the dataset may only be used for Junction 2026.
  - Never commit or publish the key, the portal password, the original unsanitised brief, `products.json`, the CSVs, the catalogue artifact or **recorded API responses**. Team decision 13 Nov: the sanitised brief and patch in `docs/brief/` are the one exception to P7, and only while the repo is private.
  - All of these live in `data/` (gitignored).
  - Committed tests use **synthetic** hand-made records (`backend/tests/synthetic/`). Tests that need real fixtures skip when `data/fixtures/` is absent.
  - Aggregate numbers are fine in docs and PRs.
  - The README explains how to fetch the data with your own event access.
