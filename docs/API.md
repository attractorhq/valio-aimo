# Aimo Connect Gateway v0.1

This is the shared **contract**, not a claim that the routes are implemented. T08 connects the contracts to FastAPI and publishes OpenAPI 3.1. Existing `/api/v1` models remain legacy until that migration. Import Python types from `app.contracts`, not `app.schemas`; frontend types come from `frontend/src/lib/contracts.ts`.

## Wire rules

- JSON field names are camelCase. Unknown model fields are rejected (`extra=forbid`). Models are frozen at the top level; the gateway must persist immutable proposal snapshots, not mutate nested lists.
- Money and quantities use Python `Decimal` and **decimal strings on the wire**. Monetary totals have two decimal places, e.g. `"1010.00"`. Unit prices, VAT rates, conversion factors and quantities may have additional precision. Consumers must not use binary floats for monetary calculations. Pydantic accepts numeric input for compatibility with the brief's examples, but always emits strings. Exported schemas describe emitted JSON.
- Currency is EUR, prices/totals exclude VAT unless explicitly named `inclVat`. VAT comes only from the price API's `tax.taxRate`. `vatRate: "13.5"` means 13.5%, not 0.135.
- Request/physical quantity units are `l | kg | pcs | cases`. `salesUnit` preserves Valio's technical unit (e.g. `ST`, `KG`). `quantitySalesUnits` is the sized sales-unit count, **not** the requested physical quantity. `itemId` links a line or excluded candidate to its request item.
- Dates are ISO `YYYY-MM-DD`, interpreted in Europe/Helsinki. Timestamps require an explicit UTC offset. Static fact retrieval times may be `null`.
- Display formats are `€1,010.00`, `Mon 16 Nov`, `Sun 15 Nov 17:00`. Python helpers are `format_money`, `format_date`, `format_time`; names are English regardless of process locale. Convert timestamps to Helsinki before calling `format_time`.
- `mode` is `LIVE` or `RECORDED`; recorded responses must include their capture time in `recordedAt` and be visibly labelled. The gateway enforces this; schemas do not implement policy or runtime state transitions.
- No real orders exist. Confirmations always display `Simulated order — mock environment`.

## Operations and channels

### Public demo access gates (T55)

- `/mcp` and agent REST writes require `Authorization: Bearer demo-agent-<customerId>` for one of the two demo buyers. Unknown/missing keys return 401. REST customer fields must match the key (403 otherwise); MCP tools use the key's customer when omitted and reject mismatches. `listMandates` on MCP returns only the key's buyer. Public read-only REST discovery remains unauthenticated.
- `GET /v0.1/proposals` lists only the authenticated agent key's buyer proposals, newest first, without capabilities.
- The approver queue and capability issuance require `X-Demo-Approver-Role: Head Chef` (school) or `Purchasing Manager` (hotel). The server checks the proposal's buyer and named role; the wrong role returns 403. A signed phone capability can supply only its issued role; an explicitly supplied wrong role still fails. The UI labels sign-in as role only, no password.
- Buyer-admin writes (including session reset) require `X-Demo-Admin-Key: demo-admin` (401 otherwise). Global reset additionally retains its architect-only reset secret. This admin key does not grant approval capability issuance or decisions.
- Audit reads, JSON/CSV exports, and `/v0.1/audit/verify` require the owning buyer's agent key for `customerId` or `proposalId`. Cross-buyer access returns 403. Global audit/verification is admin-only with `X-Demo-Admin-Key`; scoped admin reads are also allowed. Global approver proposal listing (without issuing capabilities) is admin-only.

All of these values are published demo credentials, not production identity. Production requires scoped secrets and SSO/OIDC. Approval tokens remain separate, version-bound, expiring, and single-use.

The following REST mapping is reserved for T08. All agent operations are customer-scoped; the API must bind the selected customer to the caller. The agent cannot edit mandates, choose an outcome, approve, or call confirmation. Errors/status codes and authentication are supplied by T08, not this data-contract ticket.

| Operation | REST mapping | Input | Output |
|---|---|---|---|
| `searchProducts` | `POST /v0.1/products/search` | query, requirements, language?, limit? | candidates (`Product[]`), excluded (`ExcludedProduct[]`); static only |
| `getOffer` | `POST /v0.1/offers` | `GetOfferRequest` | `Offer` |
| `createProposal` | `POST /v0.1/proposals` | `CreateProposalRequest` | `Proposal` |
| `getProposal` | `GET /v0.1/proposals/{proposalId}` | `GetProposalRequest` path parameter | `Proposal` |
| `getAuditTrail` | `GET /v0.1/audit` | `GetAuditTrailRequest`: exactly one of proposalId/customerId | `AuditTrail {entries: AuditEntry[]}` |
| `getMandate` | `GET /v0.1/mandates/{customerId}` | `GetMandateRequest` path parameter | `Mandate`, read-only |
| approver view | `GET /v0.1/approver/proposals/{proposalId}` | approver-channel demo capability | `ApproverView` |
| `decideApproval` | `POST /v0.1/approver/proposals/{proposalId}/decision` | see below | `Proposal`, through the approver channel only |
| `recheckAndConfirm` | **No public route or agent tool** | proposalId | internal server operation |

`getOffer(customerId, requestedDate, items[])` uses items shaped as:

```json
{
  "customerId": "mock-school",
  "requestedDate": "2026-11-16",
  "items": [
    {"itemId": "I1", "productId": "MOCK-PASTA", "quantity": "90", "unit": "kg"},
    {"itemId": "I2", "productId": "MOCK-OAT", "quantity": "80", "unit": "l"}
  ]
}
```

Static search narrows the shortlist first (at most 20 candidates per request item). One batched price and one availability call are made per evaluation, never for the catalogue. The upstream maximum is 1,000 unique products per batch. Duplicate IDs are deduplicated for upstream reads, not treated as multiple independent stock pools.

`createProposal(customerId, offerId, offerVersion, request)` does **not** accept an outcome, reasons, mandate version, totals, hash, or approval credential from the agent. The gateway loads those authoritative values itself, validates customer/offer/request binding, and evaluates policy. It stores the resulting `Proposal` snapshot. Repeated create operations are idempotent (implemented by T07/T08).

Request item IDs must be unique and describe every offered line. Quantities use the same physical unit as their lines; summed delivery must cover the requested quantity, with at most one sales unit of rounding per line. A mismatch returns **422** (`error.code: HTTP_ERROR`) with the item ID, line IDs and quantities, before creating a proposal or approval (e.g. `item I1 requests 10 l; offer line L1 is 5,000 l; exceeds sales-unit rounding`). Explicitly quoted partial deliveries retain their normal approval flow, but cannot be rebound to a different shortfall. An item represented only by excluded candidates retains normal policy/NO_ORDER handling. Named-product changes retain the existing substitution policy: a substitute must satisfy the hard requirements and any required approval; a product ID change cannot silently auto-confirm.

### StructuredRequest and Product

`StructuredRequest` carries the original `requestText` (data, never instructions), a Helsinki `requestedDate`, `items[]` and visible `assumptions[]`. Each item has a stable `itemId`, `what`, positive `quantity`, unit, optional explicitly named `productId`, hard `requirements[]`, and soft `preferences[]`.

- Requirement: `{kind: "CLAIM", code: "FREE_FROM_GLUTEN"}` or `{kind: "AVOID_ALLERGEN", code: "AM"}`. A free-from claim is not an allergen-avoidance rule. Lactose-free does not imply milk-free. Certification and origin wishes (`MARINE_STEWARDSHIP_COUNCIL_LABEL` for MSC, `AQUACULTURE_STEWARDSHIP_COUNCIL` for ASC, `EU_ORGANIC_FARMING` for organic/luomu, `FAIR_TRADE_MARK` for Fairtrade/Reilu kauppa, and `FINNISH_ORIGIN` for Finnish/kotimainen/suomalainen) are also parsed and enforced as `CLAIM` hard requirements against `labels` and `countryOfOrigin`, excluding products without the required label/origin with `CLAIM_MISSING`.
- Preference: `{kind: "BRAND" | "ORGANIC" | "ORIGIN" | "LABEL" | "FAT_PERCENT" | "PACK_SIZE" | "OTHER", value: string}`. Preferences never weaken hard requirements.
- A contradiction or unsafe ambiguity must be clarified, not silently assumed; policy/parser tickets implement that behaviour.

Normalised `Product` retains multilingual names, category, food status, technical/base units, exact unit conversions, allowed lot size, optional content per sales unit, variable-weight flag, allergen declarations, claim/label codes, origin, ingredients, substitution product IDs, deposits, and trust-labelled facts. A missing content value is `null`, not an invented size. `hasAllergenDeclaration` distinguishes absent data from an implicit not-listed allergen. Declaration status is `CONTAINS | MAY_CONTAIN | FREE_FROM | NOT_LISTED | UNDECLARED | UNKNOWN`; the `evidence` TrustLabel preserves provenance.

X99 proves free-from **only for EU-14 groups and their subcodes** (P4), not lactose or other non-EU-14 codes. `FREE_FROM_LACTOSE` alone proves lactose-free. Catalogue/trust logic implements these rules; the models only describe the evidence.

### Offer and proposal binding

`Offer` includes `offerId`, positive `offerVersion`, `offerHash` (64 lowercase hex characters, SHA-256), customer, requested date, creation/expiry, mode/capture time, lines, excluded products, totals, and detached Ed25519 receipt fields `kid` and `signature` (T67).

Each `OfferLine` fixes product/name, item link, sales-unit count, delivered physical quantity, content basis, unit price, line total, API VAT rate, delivery date, cut-off, facts, reasons and optional substitution relation. `facts[]` includes price, VAT, stock, date/cut-off and every decisive requirement with its evidence. Empty/unknown data must not be interpreted as proof.

`offerHash` commits to the ordered line snapshots: product IDs, quantities, units, unit prices, VAT, dates, cut-offs and trust labels. T06 supplies canonical hashing; the schema only requires the digest shape. Offer expiry is `min(createdAt + 15 minutes, price validTo across lines)`.

`Proposal` repeats the frozen lines/totals and binds them to `customerId + mandateVersion + request + offerId + offerVersion + offerHash`. It adds a stable `proposalId`, state, server `decision`, order-level `reasons[]`, optional `approverRole`, detached Ed25519 decision receipt (`kid`, `signature`), and — once `state == "CONFIRMED_SIMULATED"` — `confirmationSignature`. `Decision` is the standalone shape `{decision: AUTO|ASK|BLOCK, reasons: ReasonCode[], approverRole?: string|null}`. In a Proposal those fields are **flat**, not a nested object. Excluded candidates and their reasons stay in `excluded[]`; they do not pollute an otherwise valid proposal's ASK reasons.

`reasonDetails` (optional, T97) maps a reason code to the server's own wording where the code alone is not enough. Example: an `OVER_AUTO_LIMIT` caused by the same-day total reads `"€297.96 already committed for delivery on Mon 16 Nov; with this order €595.92 > €500.00 auto-approve limit"`. It is informational; `decision` and `reasons[]` stay authoritative.

After a change, the old proposal remains visible as `SUPERSEDED`, with `supersededBy` pointing to a **new proposal ID**, bound to a new offer version/hash. No prior approval transfers to it. `decision` records the policy outcome; `state` records lifecycle progress. Rejection does not rewrite an ASK outcome into BLOCK.

### State machine (P1.4)

```text
OFFERED → PROPOSED → AUTO_APPROVED → RECHECKED → CONFIRMED_SIMULATED
                   ↘ PENDING_APPROVAL → APPROVED → RECHECKED → CONFIRMED_SIMULATED
                   ↘ BLOCKED
PENDING_APPROVAL → REJECTED | EXPIRED
Any state before CONFIRMED_SIMULATED → SUPERSEDED when the re-check fails
An expired proposal → EXPIRED (before confirmation)
```

Every transition emits an `AuditEntry`. `OFFERED` is the initial lifecycle marker; `Offer` itself does not need a state field. The server alone transitions to confirmation. Before **every** confirmation it re-fetches price/availability, checks mandate version, prices, sufficient verified quantity, accepted delivery date/cut-off/sales block, and hard-requirement evidence. Any changed binding or decisive term voids the approval and triggers supersession and a fresh decision. An explicitly accepted later/partial delivery is its own immutable proposal; rechecking cannot silently substitute new terms.

### Approval channel only

`ApproverView` is the **only model containing an approval token, URL or QR payload**. It wraps a `Proposal`, named `Approver`, optional `approvalStatement` (plain-language summary of what approving means, e.g. `"Approving means 95 kg instead of 120 kg, on Monday instead of today."`), `approvalToken`, optional `approvalUrl`/`qrPayload`, `expiresAt`, and the exact permission notice `Single-use approval link`. Neither this view nor any of its capability fields may be embedded in an agent response, agent tool output, shared log, explanation, or audit entry. Runtime serializers must also keep credentials out of arbitrary fact values and free text; schema inspection alone is not a security boundary.

The separate approver panel/page sends `decideApproval(proposalId, offerVersion, approvalToken, action, comment?)` **only on the approver route**, where `action` is `APPROVE | REJECT`. This credential-bearing transport is deliberately not an agent request model. The server verifies single use, expiry, named role, and binding to `proposalId + offerVersion + offerHash`. Only the exact displayed proposal can be approved; BLOCK is never approvable. Approval triggers a re-check, not immediate confirmation. There is no partial-lines approval action.

Agent-facing pending responses contain only the proposal, `state: PENDING_APPROVAL`, reasons, and `approverRole`; no approval capability or approve button. The demo actor is `human:Head Chef (link)`. Production requires authenticated named approvers (SSO/OIDC); demo links are not production authentication.

### Mandate and audit

`Mandate.version` is a positive integer. It holds customer/organisation/segment, STANDARD or STRICT allergen mode, net auto/hard limits, substitute/supplier-stock controls, reliability threshold (0–1), cut-off enforcement, forbidden GS1 allergens and named approver. `autoAcceptAgentSubstitutes` is always false. A buyer-admin change increments version and emits an audit event; the agent can never update it.

**Scope and validity (T68).** Three optional fields limit *what* and *until when*; the demo mandates M1/M2 leave them empty, so they behave as before:

- `allowedCategories: string[]`: catalogue category code prefixes (e.g. `"211"`). Empty means any category. A line whose product category starts with none of them is EXCLUDED with `CATEGORY_NOT_ALLOWED` (rule L15, not approvable).
- `validFrom` / `validTo: date | null`: first and last day (Europe/Helsinki calendar day, both inclusive). Outside the window, every order BLOCKs with `MANDATE_NOT_VALID` (rule O0, the first reason) until a buyer admin renews the mandate. `validTo` must not be before `validFrom`.

The buyer admin sets them with `PATCH /v0.1/admin/mandates/{customerId}` (`allowedCategories`, `validFrom`, `validTo`; an explicit `null` clears a bound). The change is versioned and audited like any other mandate change, and open proposals are re-decided.

**Restaurant preferences (#174).** `restaurantPreferences: string` (≤ 1,000 characters, default `""`) holds soft, free-text kitchen preferences saved by the buyer admin, e.g. "Our head chef prefers cream rather than milk for cooking and baking, when appropriate." They are set with the same `PATCH` (`""` clears them) and are versioned and audited like any other mandate change.

- They go to the request-interpreting LLM only, as delimited data (`restaurantPreferences` next to `requestText`). Instruction-like clauses are cut before the model sees them. They never reach the policy, so AUTO / ASK / BLOCK, limits, requirements, allergens and approvals are unchanged by them.
- A product the buyer named is never replaced: if the model's reading changes a named product (milk → cream), the named product is kept with the note "Restaurant preference · Not applied: milk was requested explicitly, so it was kept."
- The model reports what it used as `restaurantPreferenceNotes` ("Applied: …", "Not applied: …", "Unclear: …"). They are added to `StructuredRequest.assumptions` as "Restaurant preference · …", shown in the UI as read-only notes.
- Without a model reading (no LLM configured, timeout, invalid output), the deterministic parser ignores them and the request says "Restaurant preferences not applied: the request was read without the language model." 

`AuditEntry` records timestamp, actor, event/summary, customer/proposal/offer binding, mandate version, optional old/new state, rule, reason codes, facts, optional human comment, the SHA-256 chain links (`prevHash`, `hash`, starting from `0`×64 genesis), and detached Ed25519 receipt fields (`kid`, `signature`) on offer, decision, and confirmation transitions. There is no arbitrary credential-bearing metadata field. Rejection/approval comments and request text remain untrusted data.

- **Chain verification & export (T28, T30, T67):** `GET /v0.1/audit/verify` verifies the SHA-256 hash chain on the server, and `GET /v0.1/audit/export?proposalId=…|customerId=…&format=json|csv` exports the full trail including each entry's `prevHash`, `hash`, `kid`, and `signature` (with spreadsheet formula cells neutralised in CSV).
- **Published keys, discovery & offline verification (T67):** `GET /.well-known/aimo-connect-keys.json` publishes the gateway's Ed25519 public key as an RFC 8037 OKP JWK Set (`kty: "OKP"`, `crv: "Ed25519"`, `alg: "EdDSA"`, `kid`, `x`), and `GET /.well-known/aimo-connect.json` points to OpenAPI, MCP, `/spec`, and the JWK Set. Detached base64url signatures cover canonical UTF-8 JSON (`sort_keys=True`, `(",", ":")` separators) for `aimo-connect.offer.v0.1`, `aimo-connect.decision.v0.1`, `aimo-connect.confirmation.v0.1`, and `aimo-connect.audit-entry.v0.1`. `scripts/verify-audit <export.json> [--keys <jwks>]` verifies both the SHA-256 chain and Ed25519 signatures offline using the Python standard library alone.

## Reason-code registry

The canonical registry is `app.contracts.REASON_REGISTRY`; generated TypeScript exports the same copy. Each entry has `code`, default `effect`, `allowedEffects`, `approvable`, `chipText`, and `template`. Effects are `ASK | EXCLUDE | BLOCK | INFO | NO_ORDER`; `EXCLUDE` removes the candidate, not the entire viable order. `NO_ORDER` means no confirmable proposal: do not invent a confirmation or ask a human to waive missing evidence. T05 computes decisions and applies context, not the registry.

| Default effect | Codes | Approvable |
|---|---|---|
| ASK | `OVER_AUTO_LIMIT`, `DATE_LATER`, `PARTIAL_AVAILABILITY`, `SUPPLIER_STOCK_UNKNOWN`, `VALIO_SUBSTITUTE`, `AGENT_SUBSTITUTE`, `LOW_RELIABILITY`, `QUANTITY_BASIS_WEAK` | yes |
| EXCLUDE | `CLAIM_MISSING`, `ALLERGEN_CONTAINS`, `ALLERGEN_MAY_CONTAIN`, `ALLERGEN_UNKNOWN`, `ALLERGEN_IMPLICIT`, `DATA_CONFLICT`, `CUTOFF_PASSED`, `UNIT_MISMATCH`, `NOT_AVAILABLE`, `SALES_BLOCKED`, `PRICE_UNAVAILABLE`, `SUBSTITUTE_REJECTED`, `DEPOSIT_COST_UNVERIFIED`, `QUANTITY_BASIS_UNKNOWN`, `CATEGORY_NOT_ALLOWED` | no |
| BLOCK | `OVER_HARD_LIMIT`, `MANDATE_NOT_VALID` | no |
| INFO | `SPLIT`, `INSTRUCTION_IGNORED`, `REQUOTED`, `VARIABLE_WEIGHT_UNVERIFIED` | no |
| NO_ORDER | `NO_CANDIDATE`, `CLARIFICATION_NEEDED` | no |

Contextual effects are explicit: `VALIO_SUBSTITUTE` may be INFO when the mandate auto-accepts a compliant Valio substitute. `ALLERGEN_CONTAINS` may BLOCK a specifically named product forbidden by the mandate. Neither exception lets a human waive a hard requirement. Partial availability is ASK **only for a separately versioned, explicitly partial proposal**; supplier stock only with an open cut-off; every substitute must prove every hard requirement.

The P2.6 cut-off template is exactly:

> Order cut-off was {closingTime}, so {availableDate} delivery can't be promised. Re-quoting.

Two legacy findings are settled here:

1. **Unpriced deposits:** `DEPOSIT_COST_UNVERIFIED` → EXCLUDE, not approvable, because the total is incomplete. `Deposit` retains product ID/quantity and an optional explicit price; a missing price is not zero. Do not hide a mandatory returnable charge behind approval.
2. **Variable weight:** follow brief §5.2: **1 kg per sales unit** for KG or variable-unit products. The legacy `VARIABLE_WEIGHT_UNVERIFIED` code is retained as INFO for migration only, never ASK/BLOCK; normal sizing should not emit an exception merely because weight varies. Sales-unit conversion/price validity still apply.

`QUANTITY_BASIS_UNKNOWN` names the brief's L12 unknown-content exclusion; it is distinct from the approvable name-only estimate `QUANTITY_BASIS_WEAK`.

## Schemas, generated types and examples

JSON Schemas in `spec/schemas/*.json` use Draft 2020-12, stable `urn:aimo-connect:v0.1:<Model>` identifiers, and `x-contract-version: "0.1"`. Each file is self-contained, including nested definitions. The files describe **serialized output**, including decimal strings. Regenerate both schemas and TypeScript from the worktree root:

```bash
PYTHONPATH=backend .venv/bin/python -m app.contracts.export
scripts/check
```

`backend/tests/test_contracts.py` checks schema/type drift, all reason codes, P2 non-approvability, forbidden agent schema fields (including nested models), and example round-trips. These are contract checks, not policy/transition tests.

`frontend/src/mocks/scenario-a.json` and `scenario-b.json` are hand-made **synthetic UI mocks**, with synthetic IDs/names, illustrative A/B quantities/totals, and placeholder hashes. They are not recordings or outcomes to hardcode into the gateway. B has 18 × €22.92 + 80 × €1.17 = €506.16, VAT €68.33, incl. VAT €574.49, and exactly `[OVER_AUTO_LIMIT]`; rejected substitutes stay excluded. The matching mandate mocks use M1/M2-shaped controls. Actual dataset and recorded-response evaluation stays private in ignored `data/`.

## Purchasing agent run: full or compact JSON

`POST /v0.1/agent/run` accepts `{customerId, requestText, stream?: boolean, lookupDate?: date}`.
The default `?view=full` returns the existing `AgentRunResult`, including steps, request, plan,
offer, proposal, explanation and reasons. Existing SSE/NDJSON streams are unchanged.

For external clients, `?view=compact` runs the same agent and deterministic gateway, then returns
only a decision summary (`CompactAgentRunResult`, also described in OpenAPI):

```json
{
  "customerId": "northbay-school-kitchen",
  "mode": "RECORDED",
  "recordedAt": "2026-11-13T21:52:02+02:00",
  "proposalId": "PR-EXAMPLE-v1",
  "decision": "ASK",
  "state": "PENDING_APPROVAL",
  "reasons": ["OVER_AUTO_LIMIT"],
  "totals": {"exclVat": "506.16", "vat": "68.33", "inclVat": "574.49", "currency": "EUR"},
  "lines": [
    {"productId": "MOCK-PASTA", "name": "Synthetic gluten-free macaroni", "quantitySalesUnits": "18", "salesUnit": "ST", "unitPriceExclVat": "22.92", "lineTotalExclVat": "412.56"},
    {"productId": "MOCK-OAT", "name": "Synthetic gluten-free oat drink", "quantitySalesUnits": "80", "salesUnit": "ST", "unitPriceExclVat": "1.17", "lineTotalExclVat": "93.60"}
  ],
  "approverRole": "Head Chef",
  "clarificationQuestion": null,
  "links": {"proposal": "/v0.1/proposals/PR-EXAMPLE-v1", "audit": "/v0.1/audit?proposalId=PR-EXAMPLE-v1"}
}
```

The example is synthetic. Quantities count **sales units**, with unit prices excl. VAT; decimals
remain strings. `decision`, `state`, `reasons`, totals and lines come from the final proposal, not
from a model-generated summary. `mode` and the snapshot's `recordedAt` preserve data provenance
(`recordedAt` is null when no offer/proposal snapshot exists). Relative links point only to public
agent-channel reads; follow them in the same session to retrieve the full proposal and audit.
No facts, candidate lists, approval tokens, approval links or QR payloads are included.

If no proposal is created (clarification or no candidate), `proposalId`, `decision`, `state`,
`totals`, `approverRole` and `links` are null, and `lines` is empty. `reasons` and any
`clarificationQuestion` explain what is missing; no policy outcome or zero-priced order is invented.

Compact view is non-streaming JSON only. Combining it with `stream=true` (query or body),
`Accept: text/event-stream` or `Accept: application/x-ndjson` returns HTTP 422 before any agent
work. An unsupported `view` value also returns 422. Other validation and upstream errors retain
the existing status codes and error shape.

## Retrying a delivery date

`POST /v0.1/agent/run` can return `steps[].noOrder` with the search, outcome, and optional catalogue matches for each item. If availability supports the full request on a later day, `earliestDelivery` is an ISO date. The buyer can rerun with the original `requestText` and that date as `lookupDate`.

`POST /v0.1/offers` accepts an optional `lookupDate` later than `requestedDate`. It queries availability and pricing for that later day. The offer and associated request retain `requestedDate` as the original deadline, so the gateway applies `DATE_LATER` and rechecks the later lookup when the approver signs off.

## Second client: OpenAPI + curl and `scripts/second-client`

Any outside purchasing agent can discover the gateway's operations from `/openapi.json` and run Scenario B through the public Agent channel alone, receiving the exact same deterministic decision (`ASK`, `["OVER_AUTO_LIMIT"]`, `€506.16` excl. VAT, and identical `offerHash`).

### Standalone Python client (stdlib + `urllib`, no repo imports)

```bash
scripts/second-client http://localhost:8000
# or against the deployed URL:
scripts/second-client https://valio-aimo.<account>.workers.dev
```

Output:

```text
OpenAPI:      3.1.0 (createProposal, getAuditTrail, getMandate, getOffer, getProposal, listMandates, searchProducts)
Customer:     northbay-school-kitchen (mandate v1)
Proposal:     PR-2026-11-16-...-v1 · state=PENDING_APPROVAL
Decision:     ASK
Reasons:      ["OVER_AUTO_LIMIT"]
Totals:       €506.16 excl. VAT · VAT €68.33 · €574.49 incl. VAT (EUR)
offerHash:    <64-char sha256 hex digest>
Signature:    signature verified (ac-ed25519-...)
```

### Equivalent `curl` + `jq` walkthrough (agent channel only)

```bash
API="${GATEWAY_URL:-http://localhost:8000}"
AGENT_AUTH='Authorization: Bearer demo-agent-northbay-school-kitchen'

# 1. Inspect OpenAPI 3.1 Agent channel operations
curl -sS "$API/openapi.json" | jq '[.paths | to_entries[] | .key as $p | .value | to_entries[] | select(.value.tags[]? == "Agent channel") | {operationId: .value.operationId, method: (.key | ascii_upcase), path: $p}]'

# 2. Read buyer mandate M2 (read-only)
curl -sS "$API/v0.1/mandates/northbay-school-kitchen" | jq '{customerId, version, allergenMode, autoApproveLimitEur, hardLimitEur}'

# 3. Get fixed-price offer v1 for Scenario B shortlist
OFFER="$(curl -sS -X POST "$API/v0.1/offers" \
  -H "$AGENT_AUTH" \
  -H 'Content-Type: application/json' \
  -d '{
    "customerId": "northbay-school-kitchen",
    "requestedDate": "2026-11-16",
    "items": [
      {"itemId": "I1", "productId": "474235", "quantity": "90", "unit": "kg"},
      {"itemId": "I2", "productId": "468465", "quantity": "80", "unit": "l"}
    ]
  }')"

OFFER_ID="$(jq -r .offerId <<<"$OFFER")"
OFFER_VER="$(jq -r .offerVersion <<<"$OFFER")"

# 4. Create proposal bound to offerId + offerVersion; gateway policy decides
PROPOSAL="$(curl -sS -X POST "$API/v0.1/proposals" \
  -H "$AGENT_AUTH" \
  -H 'Content-Type: application/json' \
  -d "{
    \"customerId\": \"northbay-school-kitchen\",
    \"offerId\": \"$OFFER_ID\",
    \"offerVersion\": $OFFER_VER,
    \"request\": {
      \"requestText\": \"We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink by Monday.\",
      \"requestedDate\": \"2026-11-16\",
      \"assumptions\": [],
      \"items\": [
        {\"itemId\": \"I1\", \"what\": \"macaroni\", \"quantity\": \"90\", \"unit\": \"kg\", \"requirements\": [{\"kind\": \"CLAIM\", \"code\": \"FREE_FROM_GLUTEN\"}], \"preferences\": []},
        {\"itemId\": \"I2\", \"what\": \"oat drink\", \"quantity\": \"80\", \"unit\": \"l\", \"requirements\": [{\"kind\": \"CLAIM\", \"code\": \"FREE_FROM_GLUTEN\"}], \"preferences\": []}
      ]
    }
  }")"

echo "$PROPOSAL" | jq '{decision, reasons, totals, offerHash, state}'
```

## MCP agent interface (`/mcp`, Streamable HTTP)

`/mcp` exposes the same public Agent channel over the Model Context Protocol (Streamable HTTP, JSON-RPC 2.0, protocol versions `2025-06-18`, `2025-03-26`, and `2024-11-05`).

- **Exposed tools (`tools/list`):** `listMandates`, `getMandate`, `searchProducts`, `getOffer`, `createProposal`, `getProposal`, `getAuditTrail`.
- **Forbidden on `/mcp`:** `decideApproval`, `getApproverView`, `listApproverProposals`, `recheckAndConfirm`, and `updateMandate` are never listed in `tools/list` and are rejected with JSON-RPC error `-32601` if invoked via `tools/call`.
- **Capability isolation (P3, AC15):** MCP responses never contain `approvalToken`, `approvalUrl`, or `qrPayload`.

### Connect Claude Code via `claude mcp add`

```bash
claude mcp add --transport http valio-aimo http://localhost:8000/mcp \
  --header 'Authorization: Bearer demo-agent-northbay-school-kitchen'
# or against the deployed Worker URL:
claude mcp add --transport http valio-aimo https://valio-aimo.<account>.workers.dev/mcp \
  --header 'Authorization: Bearer demo-agent-northbay-school-kitchen'
```

Asking Claude Code to run Scenario B for `northbay-school-kitchen` ("We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink by Monday, 2026-11-16") invokes `getMandate` → `searchProducts` → `getOffer` → `createProposal` and reaches `state: "PENDING_APPROVAL"` (`decision: "ASK"`, `reasons: ["OVER_AUTO_LIMIT"]`, `totals.exclVat: "506.16"`).
