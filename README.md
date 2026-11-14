# Verified or Ask · Trusted Trade Gateway

A Valio Aimo-side gateway plus a demo purchasing agent for the Valio Aimo Junction 2026 challenge. Together they run the flow **Request → Lookup → Order → Approval** against the challenge's mock Valio Aimo API.

**Core rule.** The agent may act alone only when every fact it relies on is strong enough (Agent Trust Labels v0.1) **and** the order is inside the buyer's mandate. Everything else goes to a named human, who signs off one exact proposal version with one tap.

**Every order is simulated: "Simulated order — mock environment".** Nothing is ordered from Valio. The challenge API and data are themselves synthetic.

## Run locally

Requires Python 3.12+ and Node 22+. From the repository root:

```bash
python3 -m venv .venv && .venv/bin/pip install -e 'backend[test]'
cp -n .env.example .env          # never overwrite an existing .env with keys
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm ci && npm run dev     # UI on http://localhost:5173
```

OpenAPI 3.1: `http://localhost:8000/docs` and `/openapi.json`. Health: `/health`.

**Data.** The gateway needs the catalogue artifact `data/catalog/catalog.json`, built from the challenge data with `scripts/build-catalog`. Fetch the data with your own event access first: `.venv/bin/python scripts/download_valio_data.py`. Everything under `data/` is gitignored: the dataset may only be used for Junction 2026 and is never committed.

**Valio source.** One of:
- **LIVE:** `VALIO_API_KEY` set. Live price and availability are fetched for the shortlist only.
- **RECORDED:** no key, but recorded responses in `data/fixtures/` (or `VALIO_FIXTURES_DIR`). Recorded mode is labelled everywhere and runs on the capture's clock.

Without either, `/health` reports `"status": "degraded"` and the order operations return `503 GATEWAY_UNAVAILABLE`.

`docker compose up --build` runs the production image locally. Deployment (Cloudflare Worker + Container, one origin) is in `docs/WORKFLOW.md`. State lives in memory and **resets on every restart or deploy**; a durable database is the production path.

## API v0.1

Two channels on one origin. The full contract is in `docs/API.md`, and JSON Schemas are in `spec/schemas/`.

| Channel | Operation | Route |
|---|---|---|
| Agent | `searchProducts` | `POST /v0.1/products/search[?customerId=]` |
| Agent | `getOffer` | `POST /v0.1/offers` |
| Agent | `createProposal` | `POST /v0.1/proposals` |
| Agent | `getProposal` | `GET /v0.1/proposals/{proposalId}` |
| Agent | `getAuditTrail` | `GET /v0.1/audit?proposalId=` or `?customerId=` |
| Agent | `getMandate` | `GET /v0.1/mandates`, `GET /v0.1/mandates/{customerId}` (read-only) |
| Approver | approver view | `GET /v0.1/approver/proposals[?pendingOnly=true]`, `GET /v0.1/approver/proposals/{proposalId}` |
| Approver | `decideApproval` | `POST /v0.1/approver/proposals/{proposalId}/decision` |
| Operations | health | `GET /health` |

- **The agent never decides.** The deterministic gateway policy returns AUTO, ASK or BLOCK with reason codes.
  - AUTO proposals are re-checked live and confirmed (simulated) at once.
  - ASK waits for the approver of that exact version.
  - BLOCK can never be approved.
- **The approval link never reaches the agent.** Only the approver view carries the single-use demo token, bound to `proposalId + offerVersion + offerHash`. Agent-channel responses never contain a token, URL or QR payload. `recheckAndConfirm` has no route; the server runs it before every confirmation. If anything changed, the proposal becomes SUPERSEDED.
- **Errors** look like `{"error": {"code", "message", "details"?}}`:

  | Status | Code(s) |
  |---|---|
  | 400 | `BAD_REQUEST` |
  | 403 | `APPROVAL_TOKEN_INVALID` |
  | 404 | `NOT_FOUND` |
  | 409 | `CONFLICT`, `NOT_APPROVABLE`, `TOKEN_ALREADY_USED`, `PROPOSAL_SUPERSEDED` |
  | 410 | `APPROVAL_EXPIRED` |
  | 422 | `VALIDATION_ERROR`, `NO_ORDER` (with reasons) |
  | 502 | `UPSTREAM_ERROR` |
  | 503 | `GATEWAY_UNAVAILABLE` |
- **Demo access:** there is no authentication. The approver channel is a demo link. Production needs named, authenticated approvers (SSO/OIDC) and per-customer agent credentials.

Walkthrough, scenario B (school kitchen, €500 auto-approve limit):

```bash
API=http://localhost:8000
curl -s $API/v0.1/offers -H 'Content-Type: application/json' -d '{
  "customerId": "northbay-school-kitchen", "requestedDate": "2026-11-16",
  "items": [{"itemId": "I1", "productId": "474235", "quantity": "90", "unit": "kg"},
            {"itemId": "I2", "productId": "468465", "quantity": "80", "unit": "l"}]}'
# → offerId, offerVersion, totals.exclVat
curl -s $API/v0.1/proposals -H 'Content-Type: application/json' -d '{
  "customerId": "northbay-school-kitchen", "offerId": "<offerId>", "offerVersion": 1,
  "request": {"requestText": "90 kg gluten-free macaroni and 80 l gluten-free oat drink by Monday",
    "requestedDate": "2026-11-16", "assumptions": [],
    "items": [{"itemId": "I1", "what": "macaroni", "quantity": "90", "unit": "kg",
               "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}], "preferences": []},
              {"itemId": "I2", "what": "oat drink", "quantity": "80", "unit": "l",
               "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}], "preferences": []}]}}'
# → decision ASK, reasons [OVER_AUTO_LIMIT], state PENDING_APPROVAL (no token in this response)
curl -s $API/v0.1/approver/proposals/<proposalId>          # approver only: approvalToken
curl -s $API/v0.1/approver/proposals/<proposalId>/decision -H 'Content-Type: application/json' \
  -d '{"offerVersion": 1, "approvalToken": "<token>", "action": "APPROVE"}'
# → re-checked live → CONFIRMED_SIMULATED
curl -s "$API/v0.1/audit?proposalId=<proposalId>"           # the decision log
```

### Second client (`scripts/second-client`)

`scripts/second-client` is a standalone Python script (stdlib + `urllib` only, no repo imports). It reads `/openapi.json` to discover the public Agent channel operations and runs Scenario B end-to-end through the Agent channel, printing the identical `decision`, `reasons`, `totals` and `offerHash`:

```bash
scripts/second-client http://localhost:8000
scripts/second-client https://valio-aimo.<account>.workers.dev
```

A copy-pasteable `curl` + `jq` equivalent is in `docs/API.md` → *Second client*.

## Tests

```bash
scripts/check                      # guard + ruff + pytest + frontend build (the merge gate)
cd frontend && npm run test:e2e    # Playwright on the synthetic UI mocks
cd edge && npm run check           # the edge Worker
```

Committed tests use synthetic, hand-made records only. Tests that need recorded responses read `data/fixtures/` and skip without it.

## Repository

```text
backend/app/api/       HTTP layer: agent channel, approver channel, errors, wiring
backend/app/gateway/   offers (T06), proposals, approvals and decision log (T07)
backend/app/policy/    deterministic rules L1–L14, O1–O6 and the decision
backend/app/catalog/   catalogue build, vocabulary, static search
backend/app/valio/     live and recorded Valio Aimo reads with trust labels
backend/app/agent/     request parser (LLM + deterministic fallback), O4 detector
backend/app/contracts/ v0.1 wire contracts (→ spec/schemas, frontend/src/lib/contracts.ts)
frontend/              React control room: main view, approver panel, decision log
edge/                  Cloudflare Worker: one origin for UI + API
```
