# Aimo · Trusted Trade

An agent-ready B2B food purchasing gateway, built for the Valio Aimo Junction 2026 challenge. **React + Vite, not Next.js.** The static frontend deploys to **Cloudflare Pages**; the Python/FastAPI backend runs separately.

**Two working modes:** local synthetic fixtures without credentials, or live calls to the **actual Valio Junction challenge API** with its supplied 17,546-product catalog. The challenge backend/data are themselves synthetic, not production systems. **Every order is a local simulation. Nothing is ordered from Valio.** This is a gateway and its demonstration clients, not a marketplace.

## Run locally

Requires Python **3.12+** and Node **22+**. From the repository root:

Only copy `.env.example` on a fresh clone; **do not overwrite an existing `.env` containing keys**. The example defaults to offline mock mode. For live mode, follow the next section before starting FastAPI.

```bash
cp -n .env.example .env
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

## Live Valio challenge API

The authenticated OpenAPI contract, product schema, dataset and both read operations were retrieved and verified on **13 November 2026**. Live browser testing completed catalog → price/stock → policy → revalidation → **simulated** commitment. The initial portal access problem is resolved.

Set these values in the root `.env` (keep the API key on the server):

```env
MOCK_MODE=false
VALIO_API_KEY=your-challenge-api-key
VALIO_BASE_URL=https://junction26.valioaimo.fi
VALIO_CUSTOMER_ID=junction-demo
DATABASE_URL=sqlite:////absolute/path/to/valio-aimo/backend/gateway-live.db
```

Use a **new database**, not the existing fixture database; neither catalog is deleted or overwritten. Absolute database paths keep the importer and backend pointed at the same file.

From the repository root, with the virtual environment activated:

```bash
python scripts/download_valio_data.py
python scripts/import_products.py
cd backend
uvicorn app.main:app --reload --port 8000
```

The downloader uses documented `/api/*/download` routes and skips existing files unless `--refresh` is supplied. It makes no whole-catalog price or availability queries. `product-schema.json` downloaded from the portal is optional; if present, the importer validates the original document against it. The actual Valio format and CSV columns are detected automatically; **no manual mapping is needed for the supplied files**. Import takes approximately 30–40 seconds on a typical development machine.

Restart the backend after changing `.env`. Start the frontend normally and refresh. Its badge should read **LIVE CHALLENGE API**. The built-in AI remains optional and separately requires `OPENROUTER_API_KEY`.

### A verified live demo request

Search for **`463335`**, keep **100 L**, lactose-free, next Monday and EUR 200. At the time of testing this was *Valio Eila® 3% milk drink 1 l lactose free*, **EUR 1.40 per ST**, explicitly converted to **1 L per ST**: **EUR 140** net. Availability and price are always fetched again; these observations are not a guarantee of future terms. If stock/date changes, choose a valid alternative rather than bypassing policy. The normal `milk` search checks only six matching shortlisted products in live mode.

The mock scenario buttons remain useful for deterministic fixture demos; live searches use actual supplier products, so those scenarios do not guarantee a particular policy/stock outcome.

### Confirmed contract and safety semantics

- Pricing: **`POST /api/price`** with `customer`, `requestedDate`, `products`. Uses validated `catalogPrice.value` net of tax **per supplier sales unit**, checks currency/customer/date/validity and rejects price errors. Comparison prices are not used to infer pack size.
- Availability: **`POST /api/availability`** with the same fields. Checks both sales blocks, delivery schedule, quantity, supplier sales unit and order cut-off against the response's Helsinki `timeNow`.
- **Terminal** quantity `999999` is a documented unknown-stock placeholder, never confirmed inventory. Terminal/unknown-cut-off offers cannot pass the seeded confirmed-stock requirement, including with manager approval. Late delivery and elapsed cut-offs are unavailable.
- Units: preserves original `salesUnit`, `allowedLotSize` and category codes. Translates to L/KG only with explicit consumer-level volume/net-weight plus the documented packaging hierarchy; otherwise retains the original technical unit (e.g. KI/ST/PAK). **`pack_quantity` means permitted order step**, not necessarily a whole package. The dataset explicitly allows fractional sales-unit steps.
- The API returns `sales_quantity`, `sales_unit_price` and conversion metadata. Line totals use the exact supplier sales-unit price, not the rounded unit-comparison display; net line totals round half-up to cents. Converted display prices can have eight decimal places. No density conversion or name-based pack-size inference.
- Dietary facts come only from explicit classification claims. Original allergen codes are retained. A list of positive allergens is **not** assumed exhaustive: a restriction needs explicit `FREE_FROM` evidence or escalates. Unsupported allergen identifiers remain coded/unknown.
- Unpriced returnable deposits and ambiguous variable-weight container pricing are rejected rather than producing an incomplete authorized total. VAT and delivery remain excluded, visibly, from this simulator.
- Demo policies on a freshly imported live database explicitly permit all imported supplier category codes (349 in the retrieved catalog). These are illustrative permissions, not a claim about a real customer's purchasing authority. Hotel substitutions derive only from supplied substitution rules and still undergo dietary/stock checks.
- Customer identity is server-bound. The challenge permits arbitrary customer numbers and says they do not affect results. Set optional `VALIO_CUSTOMER_IDS={"restaurant-a":"123","hotel-b":"456"}` on the backend for separate buyer bindings; the browser/model cannot select the upstream customer. Without a mapping, the configured `VALIO_CUSTOMER_ID` is used.
- No automatic retries, price cache or silent mock fallback. A failed/invalid upstream response returns a sanitized **502**. Read calls have a configurable `VALIO_TIMEOUT_SECONDS` (default 20).
- **There is no order endpoint in the verified supplier contract. Commitment is always local and clearly labelled simulated.**

`/health` reports mode and whether the live adapter has credentials; it does not continuously poll the supplier. `/api/v1/context` supplies supported units and explicit Helsinki dates.

To return to fixture mode, set `MOCK_MODE=true` and point `DATABASE_URL` at the separate original mock database, then restart. Switching modes never silently mixes catalogs.

## Deployment: Cloudflare Pages

1. Push this repository to your Git provider and create a **Cloudflare Pages** project.
2. Root directory: **`frontend`**. Build command: **`npm ci && npm run build`**. Output directory: **`dist`**. Node version: **22 or later**.
3. Set public build environment variable **`VITE_API_BASE_URL=https://your-python-backend.example`** (no trailing slash required). It must be an HTTPS backend accessible from the browser. Rebuild when this value changes.
4. Deploy FastAPI on a Python/container host (e.g. a VM, Fly.io or Render). Docker context is the repository root; Dockerfile is `backend/Dockerfile`. Expose port 8000, attach a **persistent writable volume at `/data`**, and set `DATABASE_URL=sqlite:////data/gateway.db`.
5. Set backend **`FRONTEND_ORIGINS=https://your-project.pages.dev`**; use comma-separated exact origins for custom domains. Wildcard Pages preview URLs are intentionally not trusted. Secrets belong **only on the backend**.

For live mode, initialize the persistent database **before** starting the web process: run `python scripts/download_valio_data.py --raw-dir /data/raw`, then `python scripts/import_products.py --raw-dir /data/raw` inside the built image with the same volume/environment. Alternatively, upload a locally imported SQLite database to the volume while the server is stopped. No datasets or keys are baked into the image. With Compose, use a new project/volume to retain existing mock data:

```bash
docker compose -p aimo-live build
docker compose -p aimo-live run --rm gateway python scripts/download_valio_data.py --raw-dir /data/raw
docker compose -p aimo-live run --rm gateway python scripts/import_products.py --raw-dir /data/raw
docker compose -p aimo-live up -d
```

These commands expect `MOCK_MODE=false` and `VALIO_API_KEY` in the root `.env`. Compose sets the in-container database to `/data/gateway.db`; do not use your host's absolute database path inside a container.

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

Money is decimal strings in **EUR**, calculated with Python `Decimal`. Fixture prices are per base **L/KG/EA**; live prices are converted from exact supplier sales-unit prices, net of VAT, with no delivery fee in the simulator. Quantity must be an exact order-step multiple; unsupported conversion is a **400**, never silent rounding. Missing data is `null`/unknown. Lactose-free does **not** mean free of milk allergens. Reliability is historical delivery fraction; carbon is a category average, not a per-product measurement.

### Authorization/state model

`check-offer` is only an inquiry. A proposal becomes `AUTO_APPROVED`, `NEEDS_APPROVAL`, or `REJECTED`. Only a manager may turn pending proposals into `APPROVED` or human `REJECTED`. Only authorized proposals can become `COMMITTED`.

- Category/allergen/dietary conflicts and confirmed unavailability reject. Unknown relevant dietary/allergen facts escalate. A human may explicitly accept uncertainty; that does not establish allergen safety.
- Spend/budget excess, non-authorized substitutions and unusual quantities escalate. No silent swapping. Catalog filters run before any price/stock calls; comparison is deterministic by hard constraint match, availability, price, then reliability.
- Commit rechecks current terms and policy. Changed prices/version revoke approval and require a new review; unavailable stock rejects. Human review also refreshes the offer and refuses to approve unseen changed prices.
- SQLite unique constraints enforce one order per proposal and buyer-scoped idempotency keys. Compare-and-swap proposal revisions protect concurrent transitions. Replaying the same committed key returns the same order; key reuse for a different proposal is 409.
- Audit includes request, checked offers, policy snapshot/version, decision codes, reviewer action and simulated outcome. No credentials or hidden model reasoning are logged.
- Errors: **400** invalid request/units, **401** missing demo identity, **403** role/tenant failure, **404** missing record, **409** state/terms conflict, **502** unverified or failing upstream integration.

## Optional OpenRouter assistant

Set backend `OPENROUTER_API_KEY` and optionally `LLM_MODEL` (default `z-ai/glm-5.3-flash`). Uses the Python `openai` SDK with `base_url=https://openrouter.ai/api/v1`, non-thinking tool mode, timeout 20 seconds per call, no automatic retries, maximum six model calls and twenty tool executions. The tool allowlist is:

`search_products`, `get_product_details`, `check_offer`, `create_order_proposal`, `get_proposal_status`.

Every tool's arguments are Pydantic validated; there is no arbitrary SQL/URL tool, approval tool or purchase tool. At most one proposal is created per chat request. The assistant is stateless across messages in this first version; include a complete request each time. Explanations are model-generated, while persisted proposal status and totals remain authoritative. If the model name is unavailable, credentials are missing, or the provider fails, the UI falls back to the **same deterministic guided API flow**. No live OpenRouter call is required for the demo.

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

The actual Valio format is handled by `backend/app/integrations/valio_catalog.py`. Its CSV headers are `productId,deliveryReliability` and `productId,AVG kg CO2-eq/kg`. For a **different/custom input format**, either provide a normalized array matching `ProductOut` (`backend/app/schemas.py`), or provide an explicit mapping:

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

For custom inputs, set CSV column flags to the **actual** headers. Product IDs remain original strings, quantity/unit facts must be explicit, duplicate codes are rejected, missing optional facts remain unknown, and the entire batch validates before writes. Import is an upsert, not deletion. Use a **new database** instead of mixing demo fixtures with imported products. Imported products never receive invented fixture prices or availability.

## Adapter structure

`ValioProvider` defines local `search_products` plus date- and buyer-bound `get_prices` and `check_availability`. `RealValioProvider` uses typed Pydantic responses with HTTPX and the verified vendor routes. `MockValioProvider` remains independent and reproducible. Authenticated OpenAPI and original downloads are in gitignored `data/raw/` on the development machine; no passwords or API keys are part of the repository.

## Tests

```bash
source .venv/bin/activate
python -m pytest backend/tests -q
cd frontend
npm run build
npm run test:e2e
```

Browser tests start isolated backend/frontend servers on ports **8010/5175** with their own database, without stopping your local demo; first install Chromium with `npx playwright install chromium` (Linux may also need `npx playwright install-deps chromium`). Backend tests isolate SQLite and providers; they require no credentials. Tests cover authorization, budgets, substitutions, dietary/allergen unknowns and violations, stock failure, changed terms, idempotency, audit persistence, agent allowlists, bounded tools and AI fallback.

Live adapter contract tests use HTTPX mock transports and require no external requests. They additionally cover supplier customer/date validation, missing/duplicate prices, validity windows, negative prices, 401/403/429/500 handling, stock blocks, cut-offs, unknown Terminal quantities, explicit sales-unit conversion and exact totals.

Optional real-network browser check, after downloading the datasets and configuring a valid key:

```bash
cd frontend
npm run test:live
```

This imports an isolated temporary database, starts ports **8011/5174**, and checks product 463335 end to end. It does not touch your demo database or create a supplier order. External stock, dates and prices can legitimately make this opt-in check fail. Regular browser tests remain fully mocked and do not require a key.

## Repository

```text
backend/app/       FastAPI schemas, SQLAlchemy persistence, policies, simulator, agent tools
backend/tests/     Isolated API/policy/agent tests
frontend/src/      React clients: buyer assistant, manager queue, audit, policies
frontend/e2e/      Browser acceptance tests
frontend/e2e-live/ Opt-in actual supplier API browser smoke test
scripts/           Authenticated dataset downloader and explicit importer
data/raw/          Gitignored challenge downloads
```

### Production follow-up (intentionally not in MVP)

Verified buyer/manager authentication and scoped credentials; production supplier contract review; signed/attested provenance if warranted; production per-customer credential binding; provider retry/circuit-breaking semantics; true stock reservations and fulfillment/payment/tax/deposit terms; append-only audit retention; versioned migrations; pagination; shared durable database and distributed idempotency; stricter request normalization and model evaluations. No MCP, payment layer, vector DB, background worker or multi-agent framework is needed to demonstrate this gateway.
