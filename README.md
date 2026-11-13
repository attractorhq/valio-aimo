# Aimo · Trusted Trade

An agent-ready B2B food purchasing gateway, built for the Valio Aimo Junction 2026 challenge. **React + Vite, not Next.js.** The static frontend deploys to **Cloudflare Pages**; the Python/FastAPI backend runs separately.

**Runnable without credentials.** The default catalog, fixed prices and stock are clearly labelled synthetic fixtures. **Every order is a local simulation. Nothing is ordered from Valio.** This is a gateway and its demonstration clients, not a marketplace.

## Run locally

Requires Python **3.12+** and Node **22+**. From the repository root:

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -e 'backend[test]'
cd backend
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173**. The backend creates SQLite tables, demo policies and eight reproducible fixture products on startup. OpenAPI: **http://localhost:8000/docs**. Health: **http://localhost:8000/health**. SQLite defaults to `backend/gateway.db` when launched there; use an absolute `DATABASE_URL` to keep the location consistent across commands.

Alternatively, `docker compose up --build` starts the backend with a persistent named data volume; start the frontend with the npm commands above. Do not use multiple backend replicas with SQLite.

## Two-minute demo

1. As **Restaurant A**, keep the default 100 L lactose-free milk request, EUR 200 budget, and explicitly resolved next Monday (Europe/Helsinki). Click **Find & verify options**.
2. Products show pack units, dietary facts, allergens, price source, check time and stock. Select the semi-skimmed fixture at **EUR 1.39/L**. Proposal: **EUR 139**, automatically authorized.
3. Click **Commit simulated order**. The server rechecks policy, price and availability. A clearly labelled local simulated order appears; follow **View audit trail**.
4. Return to the assistant; choose **Over limit**, verify and propose 200 L. **EUR 278** requires approval and also exceeds the request budget. Buyer commit is blocked.
5. Switch the sidebar identity to **Demo manager**, open **Approvals**, enter a review note, and approve. This explicitly authorizes the budget exception. Commit from the queue or switch back to Restaurant A.
6. Try the unavailable milk product: proposal is rejected. Try **Unknown allergens**: missing soup allergen data requires human review, never an automatic safety claim. Explicitly exclude `milk` on a milk request: always rejected, including for managers.

Hotel B has a EUR 500 limit and a single predefined milk equivalent. Use the explicit substitution checkbox to contrast buyer policies. Settings lets the demo manager change thresholds; versions invalidate prior authorizations at commit.

## Deployment: Cloudflare Pages

1. Push this repository to your Git provider and create a **Cloudflare Pages** project.
2. Root directory: **`frontend`**. Build command: **`npm ci && npm run build`**. Output directory: **`dist`**. Node version: **22 or later**.
3. Set public build environment variable **`VITE_API_BASE_URL=https://your-python-backend.example`** (no trailing slash required). It must be an HTTPS backend accessible from the browser. Rebuild when this value changes.
4. Deploy FastAPI on a Python/container host (e.g. a VM, Fly.io or Render). Docker context is the repository root; Dockerfile is `backend/Dockerfile`. Expose port 8000, attach a **persistent writable volume at `/data`**, and set `DATABASE_URL=sqlite:////data/gateway.db`.
5. Set backend **`FRONTEND_ORIGINS=https://your-project.pages.dev`**; use comma-separated exact origins for custom domains. Wildcard Pages preview URLs are intentionally not trusted. Secrets belong **only on the backend**.

`frontend/public/_redirects` supplies SPA deep-link fallback for `/approvals`, `/history`, and `/settings`. `frontend/wrangler.toml` also supports `npx wrangler pages deploy dist` after building. A Pages project needs no Next.js adapter, SSR, Workers runtime or browser API keys.

**Cloudflare hosts the frontend, not the Python/SQLite process.** Hosting the entire stack exclusively inside Cloudflare would require a separate Workers/D1 backend port; that is not included. This proof of concept uses public demo credentials and should contain only synthetic data even when publicly hosted. Add edge rate limiting/access controls before exposing an AI-funded demo widely.

## External buyer-agent API

Use `Authorization: Bearer demo-restaurant-a` (or `demo-hotel-b`). Human review requires `demo-manager`. **These public seeded identities demonstrate server-side role checks, not production authentication.** A buyer cannot select another buyer ID in a proposal or inspect another buyer's records. The demo manager can review all demo buyers.

| Operation | Route |
|---|---|
| Catalog / exact product | `GET /api/v1/products?query=milk&dietary=lactose_free`, `GET /api/v1/products/{code}` |
| Inquiry; no commitment | `POST /api/v1/check-offer` |
| Proposal + deterministic decision | `POST /api/v1/proposals` |
| Proposal list / status | `GET /api/v1/proposals`, `GET /api/v1/proposals/{id}` |
| Human sign-off / rejection | `POST /api/v1/proposals/{id}/approve`, `/reject` with `{ "note": "..." }` |
| Revalidated **local simulation** | `POST /api/v1/proposals/{id}/commit` + `Idempotency-Key` header |
| Decision evidence | `GET /api/v1/audit?proposal_id=...` |
| Demo buyer assistant | `POST /api/v1/agent/chat` with `{ "message": "..." }` |
| Policies / demo threshold change | `GET /api/v1/policies`, manager `PATCH /api/v1/policies/{buyer_id}` |
| Helsinki dates / demo capabilities | `GET /api/v1/context` |
| Readiness | `GET /health` |

Example (replace the date with the next Monday returned by `/api/v1/context`):

```bash
curl http://localhost:8000/api/v1/proposals \
  -H 'Authorization: Bearer demo-restaurant-a' \
  -H 'Content-Type: application/json' \
  -d '{"request":"100 litres of lactose-free milk","delivery_date":"2026-11-16","budget":"200.00","required_dietary":["lactose_free"],"excluded_allergens":[],"items":[{"product_code":"DEMO-MLK-02","quantity":"100","unit":"L"}]}'
```

Money is decimal strings in **EUR**, calculated with Python `Decimal`. Fixture prices are per base **L/KG/EA**, net of VAT, with no delivery fee in the simulator. Quantity must be an exact pack multiple; unsupported conversion is a **400**, never silent rounding. Missing data is `null`/unknown. Lactose-free does **not** mean free of milk allergens. Reliability is historical delivery fraction; carbon is a category average, not a per-product measurement.

### Authorization/state model

`check-offer` is only an inquiry. A proposal becomes `AUTO_APPROVED`, `NEEDS_APPROVAL`, or `REJECTED`. Only a manager may turn pending proposals into `APPROVED` or human `REJECTED`. Only authorized proposals can become `COMMITTED`.

- Category/allergen/dietary conflicts and confirmed unavailability reject. Unknown relevant dietary/allergen facts escalate. A human may explicitly accept uncertainty; that does not establish allergen safety.
- Spend/budget excess, non-authorized substitutions and unusual quantities escalate. No silent swapping. Catalog filters run before any price/stock calls; comparison is deterministic by hard constraint match, availability, price, then reliability.
- Commit rechecks current terms and policy. Changed prices/version revoke approval and require a new review; unavailable stock rejects. Human review also refreshes the offer and refuses to approve unseen changed prices.
- SQLite unique constraints enforce one order per proposal and buyer-scoped idempotency keys. Compare-and-swap proposal revisions protect concurrent transitions. Replaying the same committed key returns the same order; key reuse for a different proposal is 409.
- Audit includes request, checked offers, policy snapshot/version, decision codes, reviewer action and simulated outcome. No credentials or hidden model reasoning are logged.
- Errors: **400** invalid request/units, **401** missing demo identity, **403** role/tenant failure, **404** missing record, **409** state/terms conflict, **502** unverified or failing upstream integration.

## Optional DeepSeek assistant

Set backend `DEEPSEEK_API_KEY` and optionally `DEEPSEEK_MODEL` (default `deepseek-flash`). Uses the Python `openai` SDK with `base_url=https://api.deepseek.com`, non-thinking tool mode, timeout 20 seconds per call, no automatic retries, maximum six model calls and twenty tool executions. The tool allowlist is:

`search_products`, `get_product_details`, `check_offer`, `create_order_proposal`, `get_proposal_status`.

Every tool's arguments are Pydantic validated; there is no arbitrary SQL/URL tool, approval tool or purchase tool. At most one proposal is created per chat request. The assistant is stateless across messages in this first version; include a complete request each time. Explanations are model-generated, while persisted proposal status and totals remain authoritative. If the model name is unavailable, credentials are missing, or the provider fails, the UI falls back to the **same deterministic guided API flow**. No live DeepSeek call is required for the demo.

## Import supplied data

Downloaded files and credentials are gitignored. Put files in `data/raw/`:

```text
products.json
product-schema.json          # optional supplier JSON Schema; validates the original document if present
delivery-reliability.csv     # optional; missing products remain unknown
carbon-footprint.csv         # optional; category-average values only
```

With no `products.json`, the importer seeds fixtures **only in mock mode**:

```bash
source .venv/bin/activate
DATABASE_URL=sqlite:////absolute/path/catalog.db python scripts/import_products.py
```

The authenticated supplier schema could not be retrieved, so no vendor field names are assumed. Either provide a normalized array matching `ProductOut` (`backend/app/schemas.py`), or provide an explicit mapping:

```json
{
  "root": "products",
  "fields": {
    "code": "supplierCode", "name": "displayName", "category": "category",
    "unit": "salesUnit", "pack_quantity": "packSize",
    "allergens": "normalizedAllergens", "dietary": "normalizedDietary"
  },
  "values": { "unit": { "litre": "L", "kilogram": "KG", "piece": "EA" } }
}
```

**Those names are illustrative mapping examples, NOT claims about Valio's schema.** Dietary values must be boolean/null keyed by attributes such as `lactose_free`; allergens must be an explicit string list or unknown. Do not convert an absent supplier field into `[]` or `true`. Allergens use canonical lower-case tokens (`milk`, `gluten`, etc.); align your mapping to them.

```bash
MOCK_MODE=false DATABASE_URL=sqlite:////absolute/path/imported.db \
  python scripts/import_products.py --mapping data/raw/mapping.json \
  --reliability-code-column product_code --reliability-value-column reliability \
  --carbon-code-column product_code --carbon-value-column 'AVG kg CO2-eq/kg'
```

Set CSV column flags to the **actual** headers. Product IDs remain original strings, pack/unit facts must be explicit, duplicate codes are rejected, missing optional facts remain unknown, and the entire batch validates before writes. Import is an upsert, not deletion. Use a **new database** instead of mixing demo fixtures with imported products. Imported products do not receive invented fixture prices or availability; offer requests fail closed until the verified live adapter exists.

## Valio adapter status / remaining work

`ValioProvider` defines `search_products`, `get_prices`, and `check_availability`. `MockValioProvider` is runnable. **`RealValioProvider` deliberately fails closed with 502 for reads.** No vendor routes or formats have been invented.

During implementation, `GET https://junction26.valioaimo.fi/openapi.json` redirected to a sign-in page; a portal login attempt with the supplied password returned HTTP 403. No contract or datasets were obtained. No secrets are included in the repository.

Once access works:

1. Download authenticated OpenAPI and dataset/schema through the portal; map exact product fields and CSV columns.
2. Implement typed HTTPX responses for the documented pricing and delivery-date availability operations, with `X-API-Key`, timeouts, explicit price units/currency and source timestamps. Keep credentials on the server.
3. Contract-test unit/pack pricing, partial quantity, missing records, provider errors and dates. Never infer missing allergen facts.
4. Set `MOCK_MODE=false` against a separate imported database. Health reports that the live adapter is not ready in this version.
5. Do not add a vendor commit call unless the verified contract actually exposes it. Local commit remains labelled simulated independently of read mode.

## Tests

```bash
source .venv/bin/activate
python -m pytest backend/tests -q
cd frontend
npm run build
npm run test:e2e
```

Browser tests start isolated backend/frontend servers with their own database; first install Chromium with `npx playwright install chromium` (Linux may also need `npx playwright install-deps chromium`). Backend tests isolate SQLite and providers; they require no credentials. Tests cover authorization, budgets, substitutions, dietary/allergen unknowns and violations, stock failure, changed terms, idempotency, audit persistence, agent allowlists, bounded tools and AI fallback.

## Repository

```text
backend/app/       FastAPI schemas, SQLAlchemy persistence, policies, simulator, agent tools
backend/tests/     Isolated API/policy/agent tests
frontend/src/      React clients: buyer assistant, manager queue, audit, policies
frontend/e2e/      Browser acceptance tests
scripts/           Explicit local dataset importer
data/raw/          Gitignored challenge downloads
```

### Production follow-up (intentionally not in MVP)

Verified buyer/manager authentication and scoped credentials; reviewed vendor adapter; signed/attested provenance if warranted; per-customer pricing identity; provider retry/circuit-breaking semantics; true stock reservations and fulfillment/payment/tax terms; append-only audit retention; versioned migrations; pagination; shared durable database and distributed idempotency; stricter request normalization and model evaluations. No MCP, payment layer, vector DB, background worker or multi-agent framework is needed to demonstrate this gateway.
