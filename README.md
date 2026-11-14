# Verified or Ask · Trusted Trade Gateway

A Valio Aimo-side gateway plus a demo purchasing agent for the Valio Aimo Junction 2026 challenge. Together they run the flow **Request → Lookup → Order → Approval** against the challenge's mock Valio Aimo API.

**Core rule.** The agent may act alone only when every decisive fact is strong enough (Agent Trust Labels v0.1) **and** the order is inside the buyer's mandate. Approvable trade-offs go to a named human for one exact proposal version. Missing hard-requirement evidence is excluded; a hard-limit breach is blocked, not sent for approval.

**Every order is simulated: "Simulated order — mock environment".** Nothing is ordered from Valio. The challenge API and data are themselves synthetic.

## Demo and verification status

**Live demo:** https://valio-aimo.attractorfi.workers.dev. One origin serves the UI, `/approver`, `/v0.1` (OpenAPI at `/openapi.json`) and `/mcp`, running against the live mock API. `/health` reports the deployed build. See [the submission](docs/SUBMISSION.md), [pitch](docs/PITCH.md) and [rehearsal guide](docs/DEMO.md).

### Connect an agent (MCP)

The `/mcp` endpoint (Streamable HTTP) exposes only purchasing-agent tools: listMandates, getMandate, searchProducts, getOffer, createProposal, getProposal and getAuditTrail. Approval is not a tool, and calling it is refused.

```bash
# Claude Code
claude mcp add --transport http valio-aimo https://valio-aimo.attractorfi.workers.dev/mcp \
  --header 'Authorization: Bearer demo-agent-northbay-school-kitchen'

# Any client: initialise and list tools
curl -s https://valio-aimo.attractorfi.workers.dev/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Authorization: Bearer demo-agent-northbay-school-kitchen' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Cursor and Claude Desktop: add an HTTP MCP server with the URL `https://valio-aimo.attractorfi.workers.dev/mcp`. Demo customers are `nordic-harbour-hotels` and `northbay-school-kitchen`. A proposal that needs sign-off waits in `/approver` for a human.

Set the `Authorization` header to `Bearer demo-agent-northbay-school-kitchen` (school) or `Bearer demo-agent-nordic-harbour-hotels` (hotel). These **public demo keys** are enforced on `/mcp`, agent REST writes (including search and `/v0.1/agent/run`), and customer-scoped audit reads/exports/verification. The key determines the buyer; omitting a tool's customer uses that buyer, and passing a different buyer is refused. A missing/unknown key returns 401. REST request bodies retain their required `customerId` as a checked binding, never as caller authority.

The Approver panel asks you to choose **Head Chef** (school) or **Purchasing Manager** (hotel) and sends `X-Demo-Approver-Role`; only that buyer's queue is visible and only the proposal's named role can decide. Signed phone links carry their issued role and remain single-use. Buyer-admin writes require `X-Demo-Admin-Key: demo-admin`; `/admin` shows the public key and an **Enter demo admin key** button. Global audit access also requires that admin key. Customer/proposal audit access uses the owning buyer's agent key; a key cannot read another buyer's trail, export, or chain. These are demonstration gates, not production identity or secret credentials.

Expected reference walkthrough: **A AUTO** (€1,010.00) → **B ASK** (€506.16, only `OVER_AUTO_LIMIT`) → **C BLOCK** (€20,200.00, with `INSTRUCTION_IGNORED`). These are checks, not hardcoded decisions or guarantees about today's live data. Run pre-flight before presenting.

## Run locally

Requires Python 3.12+, Node 22+, and your own Junction 2026 Valio Aimo event access. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e 'backend[test]'
cp -n .env.example .env          # preserve any existing credentials
# Edit .env locally: set VALIO_API_KEY to your own event API key.
# OPENROUTER_API_KEY and LLM_MODEL are optional; the parser has a deterministic fallback.
.venv/bin/python scripts/download_valio_data.py
scripts/build-catalog
.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8000
```

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev                            # UI on http://localhost:5173
```

The Vite dev server proxies gateway requests to `:8000`. Open the buyer view at `http://localhost:5173/` and the separate human view at `/approver`. OpenAPI 3.1: `http://localhost:8000/docs` and `/openapi.json`.

```bash
curl -sS http://localhost:8000/health
# Require status=ok, ready=true and mode=LIVE or RECORDED; HTTP 200 alone is not readiness.
```

**Fetch data with your own event access.** Obtain your API key through the [Valio Aimo event portal](https://junction26.valioaimo.fi/); do not reuse or publish somebody else's credentials. The downloader reads `VALIO_API_KEY` from the server `.env` and downloads products plus delivery-reliability and carbon-footprint CSVs to `data/raw/` through the authenticated download endpoints. Existing files are skipped; use `--refresh` only when you intend to replace them. `scripts/build-catalog` creates the normalised `data/catalog/catalog.json` and prints recomputed statistics. Optional schema validation requires a privately obtained `data/raw/product-schema.json`.

The dataset is restricted to Junction 2026 and is not fully accurate product information. Never commit or publish `.env`, keys, portal credentials, raw data, catalogue artifacts, or recorded API responses. `data/` is gitignored. Committed test records are hand-made synthetic examples, not dataset copies. This private repository's sanitised brief is an internal exception and must be removed, including its history, before public release.

**Valio source.** One of:
- **LIVE:** `VALIO_API_KEY` set. Live price and availability are fetched for the shortlist only.
- **RECORDED:** no key, but a private capture with `manifest.json` under `data/fixtures/` (or `VALIO_FIXTURES_DIR`). To force a capture even with a key configured, launch with `VALIO_MODE=recorded`. Recorded mode runs on the capture's clock and must show its capture time; it is not live validation.

```bash
# From the repository root; obtain a permitted private capture first.
VALIO_MODE=recorded .venv/bin/uvicorn app.main:app --app-dir backend --port 8000
```

The gateway also requires the catalogue in recorded mode. Without a catalogue or usable source, `/health` reports `"status": "degraded"` and order operations return `503 GATEWAY_UNAVAILABLE`. A browser badge saying **Synthetic mock data** means a UI fallback, not a working live/recorded gateway. `MOCK_MODE` in the legacy env example does not select the v0.1 source; use `VALIO_MODE`.

`docker compose up --build` runs the production image locally. Deployment (Cloudflare Worker + Container, one origin) is in `docs/WORKFLOW.md`. State lives in memory and **resets on every restart or deploy**; a durable database is the production path.

## Architecture

```text
Buyer UI / external purchasing client
  → Agent channel (REST + OpenAPI; streamed /v0.1/agent/run for the UI)
  → parser → static catalogue shortlist (≤20 candidates per item)
  → batched Valio mock price + availability → fixed-price offer
  → deterministic gateway policy + buyer mandate → proposal: AUTO / ASK / BLOCK

Separate human Approver UI → Approver channel → exact-version approval/rejection
  → server-only re-check → simulated confirmation OR superseded proposal
All proposal transitions → decision log

Hosting design: Cloudflare Worker (UI assets + API proxy) → FastAPI Container
```

Domain modules are deterministic; adapters own network reads. Money uses `Decimal` and decimal strings on the wire. A proposal binds the customer, mandate version, structured request, offer ID/version/hash and totals. A re-check change invalidates the old approval and creates a new decision; a human cannot approve different terms implicitly.

Decisive facts carry `VERIFIED`, `DECLARED`, `ESTIMATE` or `UNKNOWN`, their source, retrieval time where available, method and any conflict. A declared product claim is not independently certified by the agent. Reliability and carbon estimates do not become verified facts.

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
  - AUTO proposals are re-checked against the selected source and confirmed (simulated) at once.
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
- **Demo access:** public demo agent keys, role-only approver sign-in, and the public admin key enforce channel/customer scope (see **Connect an agent**). They are not production authentication: anyone can choose a demo role or use a published demo key. Single-use links expire with the offer and are bound to `proposalId + offerVersion + offerHash`. The QR and phone link exist only in the Approver view, never an agent response. Audit actors describe demo roles, not authenticated identities. Production needs named approvers (SSO/OIDC), secret per-customer agent credentials, and buyer-admin authorisation. Export `APPROVAL_SIGNING_KEY` into the backend process environment if configuring a stable local signing key.

### API in 60 seconds

Open `/docs` for ready-to-edit request examples. These Bash commands use `curl` and `jq`. Set `API` to `http://localhost:8000` to use a local gateway. The sandbox header keeps the offer and proposal in the same demo session.

**Run the official hotel request**

```bash
API=${API:-https://valio-aimo.attractorfi.workers.dev}
SANDBOX="api-quickstart-$(date +%s)-$RANDOM"
curl -fsS "$API/v0.1/agent/run" -H "X-Demo-Sandbox: $SANDBOX" \
  -H 'Authorization: Bearer demo-agent-nordic-harbour-hotels' -H 'Content-Type: application/json' -d '{
  "customerId": "nordic-harbour-hotels",
  "requestText": "I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on price and availability."
}' | jq '{decision, reasons, totals: .proposal.totals}'
```

**Search and quote the school kitchen's shortlist**

```bash
curl -fsS "$API/v0.1/products/search?customerId=northbay-school-kitchen" \
  -H 'Authorization: Bearer demo-agent-northbay-school-kitchen' \
  -H "X-Demo-Sandbox: $SANDBOX" -H 'Content-Type: application/json' -d '{
    "query": "macaroni", "itemId": "I1", "limit": 5,
    "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}]
  }' | jq '.candidates[] | {productId, name}'

OFFER=$(curl -fsS "$API/v0.1/offers" -H "X-Demo-Sandbox: $SANDBOX" \
  -H 'Authorization: Bearer demo-agent-northbay-school-kitchen' -H 'Content-Type: application/json' -d '{
  "customerId": "northbay-school-kitchen", "requestedDate": "2026-11-16",
  "items": [
    {"itemId": "I1", "productId": "474235", "quantity": "90", "unit": "kg"},
    {"itemId": "I2", "productId": "468465", "quantity": "80", "unit": "l"}
  ]
}')
printf '%s\n' "$OFFER" | jq '{offerId, offerVersion, requestedDate, totals}'
```

**Create a proposal from that offer**

```bash
printf '%s\n' "$OFFER" | jq '{
  customerId, offerId, offerVersion,
  request: {
    requestedDate,
    requestText: "We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink.",
    items: [
      {itemId: "I1", productId: "474235", what: "macaroni", quantity: "90", unit: "kg",
       requirements: [{kind: "CLAIM", code: "FREE_FROM_GLUTEN"}]},
      {itemId: "I2", productId: "468465", what: "oat drink", quantity: "80", unit: "l",
       requirements: [{kind: "CLAIM", code: "FREE_FROM_GLUTEN"}]}
    ]
  }
}' | curl -fsS "$API/v0.1/proposals" -H "X-Demo-Sandbox: $SANDBOX" \
  -H 'Authorization: Bearer demo-agent-northbay-school-kitchen' \
  -H 'Content-Type: application/json' --data-binary @- | jq '{proposalId, decision, reasons, state}'
```

Submit the proposal before the offer expires. The delivery date in these examples matches the challenge dataset. After the live order cut-off, use a gateway serving the recorded capture.

Walkthrough for human approval of Scenario B (school kitchen, €500 auto-approve limit):

```bash
API=http://localhost:8000
AGENT_AUTH='Authorization: Bearer demo-agent-northbay-school-kitchen'
curl -s $API/v0.1/offers -H "$AGENT_AUTH" -H 'Content-Type: application/json' -d '{
  "customerId": "northbay-school-kitchen", "requestedDate": "2026-11-16",
  "items": [{"itemId": "I1", "productId": "474235", "quantity": "90", "unit": "kg"},
            {"itemId": "I2", "productId": "468465", "quantity": "80", "unit": "l"}]}'
# → offerId, offerVersion, totals.exclVat
curl -s $API/v0.1/proposals -H "$AGENT_AUTH" -H 'Content-Type: application/json' -d '{
  "customerId": "northbay-school-kitchen", "offerId": "<offerId>", "offerVersion": 1,
  "request": {"requestText": "90 kg gluten-free macaroni and 80 l gluten-free oat drink by Monday",
    "requestedDate": "2026-11-16", "assumptions": [],
    "items": [{"itemId": "I1", "what": "macaroni", "quantity": "90", "unit": "kg",
               "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}], "preferences": []},
              {"itemId": "I2", "what": "oat drink", "quantity": "80", "unit": "l",
               "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}], "preferences": []}]}}'
# → decision ASK, reasons [OVER_AUTO_LIMIT], state PENDING_APPROVAL (no token in this response)
curl -s $API/v0.1/approver/proposals/<proposalId> -H 'X-Demo-Approver-Role: Head Chef' # approver only: approvalToken
curl -s $API/v0.1/approver/proposals/<proposalId>/decision -H 'Content-Type: application/json' \
  -H 'X-Demo-Approver-Role: Head Chef' \
  -d '{"offerVersion": 1, "approvalToken": "<token>", "action": "APPROVE"}'
# → source re-checked → CONFIRMED_SIMULATED, or SUPERSEDED if decisive terms changed
curl -s "$API/v0.1/audit?proposalId=<proposalId>" -H "$AGENT_AUTH" # the customer's decision log
```

### Second client (`scripts/second-client`)

`scripts/second-client` is a standalone Python script (stdlib + `urllib` only, no repo imports). It reads `/openapi.json`, runs Scenario B through the Agent channel to `PENDING_APPROVAL`, and prints `decision`, `reasons`, `totals` and `offerHash`. It cannot approve. The same bound offer produces the same deterministic decision; independently fetched live offers need not share a hash because facts and retrieval times may differ.

```bash
scripts/second-client http://localhost:8000
# Once the architect supplies a verified deployment URL:
# scripts/second-client "$GATEWAY_URL"
```

A copy-pasteable `curl` + `jq` equivalent is in `docs/API.md` → *Second client*.

## Tests

```bash
scripts/check                      # guard + ruff + pytest + frontend build (the merge gate)
scripts/eval                       # 24-row report; inspect source, failures AND pending rows
scripts/preflight                  # live mock API A/B/C/B2; inspect GREEN/RED, not just exit status
scripts/demo                       # local pre-flight + reset + 3 rehearsals + second client
# In frontend/ (install Chromium once with npx playwright install chromium):
# npm run test:e2e                 # synthetic UI tests plus the demo flow
# npm run test:live                # opt-in gateway browser tests
# In edge/: npm ci && npm run check
```

Committed tests use synthetic, hand-made records only. Tests that need recorded responses read `data/fixtures/` and skip without it.

### Evaluation: 8/24, not 24/24

On 14 Nov 2026, `scripts/eval` on this developer checkout reports **8/24**: **8/8 active rows passed, 0 failed, 16 pending**. Source: **`REFERENCE_FIXTURES`**, frozen at `2026-11-13T21:52:02+02:00`; private recorded responses were absent. This is a reference-fixture check, not a recorded-data or deployed score. The active rows are 1, 2, 4, 8 and 21–24; the remaining rows await the missing evaluation definitions in the internal brief. Pending does not mean passed. The suite bypasses free-text parsing by using explicit shortlists, so browser/agent tests provide separate end-to-end evidence.

To report a recorded-data score, supply permitted private captures and run `scripts/eval --fixtures-dir /path/to/private/fixtures`; retain the source, capture time and pending count alongside N/24. Do not infer all 24 passed from exit code zero.

For context, the architect's [T18 review](https://github.com/attractorhq/valio-aimo/pull/62#issuecomment-6870722109) reports three consecutive local live rehearsals, three on a real recorded capture, and 46/46 browser tests. Those are local results, not deployment evidence.

Before presenting, run `scripts/preflight`. GREEN permits a live walkthrough; RED means show the differences and use an explicitly labelled private recorded capture, with its capture time. `scripts/preflight --reference` is only an offline self-test. The browser's simulated passed-cut-off preview is not proof that the backend changed source; check backend health and launch the actual recorded source as above. See [the stage guide](docs/DEMO.md) for rehearsal timing.

## Assumptions & observed behaviour

Supplier mock observations below were recorded on **13 Nov 2026**, not promises about current responses. Re-run pre-flight before relying on them.

| Observation or constraint | Gateway consequence |
|---|---|
| Availability ignored `requestedDate` in observed calls and returned Mon 16 Nov 2026. | Validate the returned date; never invent delivery on the requested date. |
| Price responses could be outside `validFrom..validTo`. | Check validity independently; an API response alone does not prove an applicable price. |
| Unknown IDs returned `error: true` and empty `scheduleLines`. | Treat absent price/availability as missing evidence, not zero cost or available stock. |
| Terminal stock `999999` is a supplier-stock sentinel. | Quantity is `UNKNOWN`, not 999,999 verified units; a passed cut-off remains non-approvable. |
| Many Monday stock cut-offs were Sun 15 Nov 17:00 EET. | Once passed, Monday delivery cannot be promised; report RED rather than alter the live clock. |
| Lactose-free is not milk-free; oat drink is not automatically gluten-free. | Require the appropriate declared claim/allergen evidence. X99 only proves EU-14 free-from, never lactose-free. |
| A supplier-listed substitute can violate the buyer's requirements. | Recheck every hard requirement; exclude the substitute rather than silently swap. |
| No real order API is supplied; demo state is process-local and volatile. | Confirmations are simulated and logs/proposals disappear on restart or deploy. |

## Path to production (not delivered production capabilities)

1. **Identity and authority:** per-customer agent credentials, authenticated named approvers via SSO/OIDC, buyer-admin authorisation and audit identities from authentication. Replace demo links as the authority mechanism.
2. **Durability:** transactional proposal, approval-token consumption, mandate and audit storage in a durable database, with recovery, concurrency controls, retention and monitoring. The current in-memory state is not a production record.
3. **Real fulfilment:** integrate a supplier order API and explicit idempotency/reconciliation. Challenge confirmations do not create orders or payments.
4. **Open agent access:** REST/OpenAPI 3.1, JSON Schemas and an MCP endpoint (`/mcp`, agent-channel tools only) work today. Production adds scoped agent credentials per buyer and keeps `decideApproval` and confirmation off every agent interface.
5. **Data assurance:** validate supplier declarations and freshness under production agreements; surface unresolved conflicts and missing evidence instead of allowing a human to waive them. The demo already seals the decision log with a SHA-256 hash chain (`/v0.1/audit/verify`) and exports it as JSON/CSV; production moves it to durable, append-only storage.

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
