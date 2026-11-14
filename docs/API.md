# Verified or Ask Gateway v0.1

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

### StructuredRequest and Product

`StructuredRequest` carries the original `requestText` (data, never instructions), a Helsinki `requestedDate`, `items[]` and visible `assumptions[]`. Each item has a stable `itemId`, `what`, positive `quantity`, unit, optional explicitly named `productId`, hard `requirements[]`, and soft `preferences[]`.

- Requirement: `{kind: "CLAIM", code: "FREE_FROM_GLUTEN"}` or `{kind: "AVOID_ALLERGEN", code: "AM"}`. A free-from claim is not an allergen-avoidance rule. Lactose-free does not imply milk-free.
- Preference: `{kind: "BRAND" | "ORGANIC" | "ORIGIN" | "LABEL" | "FAT_PERCENT" | "PACK_SIZE" | "OTHER", value: string}`. Preferences never weaken hard requirements.
- A contradiction or unsafe ambiguity must be clarified, not silently assumed; policy/parser tickets implement that behaviour.

Normalised `Product` retains multilingual names, category, food status, technical/base units, exact unit conversions, allowed lot size, optional content per sales unit, variable-weight flag, allergen declarations, claim/label codes, origin, ingredients, substitution product IDs, deposits, and trust-labelled facts. A missing content value is `null`, not an invented size. `hasAllergenDeclaration` distinguishes absent data from an implicit not-listed allergen. Declaration status is `CONTAINS | MAY_CONTAIN | FREE_FROM | NOT_LISTED | UNDECLARED | UNKNOWN`; the `evidence` TrustLabel preserves provenance.

X99 proves free-from **only for EU-14 groups and their subcodes** (P4), not lactose or other non-EU-14 codes. `FREE_FROM_LACTOSE` alone proves lactose-free. Catalogue/trust logic implements these rules; the models only describe the evidence.

### Offer and proposal binding

`Offer` includes `offerId`, positive `offerVersion`, `offerHash` (64 lowercase hex characters, SHA-256), customer, requested date, creation/expiry, mode/capture time, lines, excluded products and totals.

Each `OfferLine` fixes product/name, item link, sales-unit count, delivered physical quantity, content basis, unit price, line total, API VAT rate, delivery date, cut-off, facts, reasons and optional substitution relation. `facts[]` includes price, VAT, stock, date/cut-off and every decisive requirement with its evidence. Empty/unknown data must not be interpreted as proof.

`offerHash` commits to the ordered line snapshots: product IDs, quantities, units, unit prices, VAT, dates, cut-offs and trust labels. T06 supplies canonical hashing; the schema only requires the digest shape. Offer expiry is `min(createdAt + 15 minutes, price validTo across lines)`.

`Proposal` repeats the frozen lines/totals and binds them to `customerId + mandateVersion + request + offerId + offerVersion + offerHash`. It adds a stable `proposalId`, state, server `decision`, order-level `reasons[]` and optional `approverRole`. `Decision` is the standalone shape `{decision: AUTO|ASK|BLOCK, reasons: ReasonCode[], approverRole?: string|null}`. In a Proposal those fields are **flat**, not a nested object. Excluded candidates and their reasons stay in `excluded[]`; they do not pollute an otherwise valid proposal's ASK reasons.

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

`ApproverView` is the **only model containing an approval token, URL or QR payload**. It wraps a `Proposal`, named `Approver`, `approvalToken`, optional `approvalUrl`/`qrPayload`, `expiresAt`, and the exact permission notice `Demo approval link — single use`. Neither this view nor any of its capability fields may be embedded in an agent response, agent tool output, shared log, explanation, or audit entry. Runtime serializers must also keep credentials out of arbitrary fact values and free text; schema inspection alone is not a security boundary.

The separate approver panel/page sends `decideApproval(proposalId, offerVersion, approvalToken, action, comment?)` **only on the approver route**, where `action` is `APPROVE | REJECT`. This credential-bearing transport is deliberately not an agent request model. The server verifies single use, expiry, named role, and binding to `proposalId + offerVersion + offerHash`. Only the exact displayed proposal can be approved; BLOCK is never approvable. Approval triggers a re-check, not immediate confirmation. There is no partial-lines approval action.

Agent-facing pending responses contain only the proposal, `state: PENDING_APPROVAL`, reasons, and `approverRole`; no approval capability or approve button. The demo actor is `human:Head Chef (demo, link-based)`. Production requires authenticated named approvers (SSO/OIDC); demo links are not production authentication.

### Mandate and audit

`Mandate.version` is a positive integer. It holds customer/organisation/segment, STANDARD or STRICT allergen mode, net auto/hard limits, substitute/supplier-stock controls, reliability threshold (0–1), cut-off enforcement, forbidden GS1 allergens and named approver. `autoAcceptAgentSubstitutes` is always false. A buyer-admin change increments version and emits an audit event; the agent can never update it.

`AuditEntry` records timestamp, actor, event/summary, customer/proposal/offer binding, mandate version, optional old/new state, rule, reason codes, facts, and optional human comment. There is no arbitrary credential-bearing metadata field. Rejection/approval comments and request text remain untrusted data. Hash chaining and audit export are later tickets.

## Reason-code registry

The canonical registry is `app.contracts.REASON_REGISTRY`; generated TypeScript exports the same copy. Each entry has `code`, default `effect`, `allowedEffects`, `approvable`, `chipText`, and `template`. Effects are `ASK | EXCLUDE | BLOCK | INFO | NO_ORDER`; `EXCLUDE` removes the candidate, not the entire viable order. `NO_ORDER` means no confirmable proposal: do not invent a confirmation or ask a human to waive missing evidence. T05 computes decisions and applies context, not the registry.

| Default effect | Codes | Approvable |
|---|---|---|
| ASK | `OVER_AUTO_LIMIT`, `DATE_LATER`, `PARTIAL_AVAILABILITY`, `SUPPLIER_STOCK_UNKNOWN`, `VALIO_SUBSTITUTE`, `AGENT_SUBSTITUTE`, `LOW_RELIABILITY`, `QUANTITY_BASIS_WEAK` | yes |
| EXCLUDE | `CLAIM_MISSING`, `ALLERGEN_CONTAINS`, `ALLERGEN_MAY_CONTAIN`, `ALLERGEN_UNKNOWN`, `ALLERGEN_IMPLICIT`, `DATA_CONFLICT`, `CUTOFF_PASSED`, `UNIT_MISMATCH`, `NOT_AVAILABLE`, `SALES_BLOCKED`, `PRICE_UNAVAILABLE`, `SUBSTITUTE_REJECTED`, `DEPOSIT_COST_UNVERIFIED`, `QUANTITY_BASIS_UNKNOWN` | no |
| BLOCK | `OVER_HARD_LIMIT` | no |
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

JSON Schemas in `spec/schemas/*.json` use Draft 2020-12, stable `urn:verified-or-ask:v0.1:<Model>` identifiers, and `x-contract-version: "0.1"`. Each file is self-contained, including nested definitions. The files describe **serialized output**, including decimal strings. Regenerate both schemas and TypeScript from the worktree root:

```bash
PYTHONPATH=backend .venv/bin/python -m app.contracts.export
scripts/check
```

`backend/tests/test_contracts.py` checks schema/type drift, all reason codes, P2 non-approvability, forbidden agent schema fields (including nested models), and example round-trips. These are contract checks, not policy/transition tests.

`frontend/src/mocks/scenario-a.json` and `scenario-b.json` are hand-made **synthetic UI mocks**, with synthetic IDs/names, illustrative A/B quantities/totals, and placeholder hashes. They are not recordings or outcomes to hardcode into the gateway. B has 18 × €22.92 + 80 × €1.17 = €506.16, VAT €68.33, incl. VAT €574.49, and exactly `[OVER_AUTO_LIMIT]`; rejected substitutes stay excluded. The matching mandate mocks use M1/M2-shaped controls. Actual dataset and recorded-response evaluation stays private in ignored `data/`.

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
```

### Equivalent `curl` + `jq` walkthrough (agent channel only)

```bash
API="${GATEWAY_URL:-http://localhost:8000}"

# 1. Inspect OpenAPI 3.1 Agent channel operations
curl -sS "$API/openapi.json" | jq '[.paths | to_entries[] | .key as $p | .value | to_entries[] | select(.value.tags[]? == "Agent channel") | {operationId: .value.operationId, method: (.key | ascii_upcase), path: $p}]'

# 2. Read buyer mandate M2 (read-only)
curl -sS "$API/v0.1/mandates/northbay-school-kitchen" | jq '{customerId, version, allergenMode, autoApproveLimitEur, hardLimitEur}'

# 3. Get fixed-price offer v1 for Scenario B shortlist
OFFER="$(curl -sS -X POST "$API/v0.1/offers" \
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
