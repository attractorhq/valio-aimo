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
- Audit reads, JSON/CSV exports, and `/v0.1/audit/verify` require the owning buyer's agent key for `customerId` or `proposalId`. Cross-buyer access returns 403. Global audit reads are admin-only with `X-Demo-Admin-Key`; scoped admin reads are also allowed. Global approver proposal listing (without issuing capabilities) is admin-only.
- **Buyer-verifiable chain (T110).** `GET /v0.1/audit/verify` with only the buyer's agent key (`Authorization: Bearer demo-agent-<customerId>`, no `customerId`/`proposalId`) verifies **that buyer's entries only** and returns `verified`, `chainLength` and `headHash` of the buyer's own chain. The admin key without parameters keeps the full verify over every buyer. `AuditChainVerification` echoes the checked scope in two optional fields: `customerId` (null for the admin's full verify) and `proposalId` (set for a per-proposal verify). The head hash is not signed; a buyer can store it and compare it on a later verify. 401 and 403 messages name the header to send (`Authorization: Bearer demo-agent-<customerId>` or `X-Demo-Admin-Key: demo-admin`).
- **Private sessions (T116).** Every API client works in its own private session (sandbox): its mandates, offers, proposals, daily totals, audit chain and suspension. A cookieless call without a session gets a new one, and every REST/MCP response carries its id in the `X-Demo-Sandbox` header. **Send it back as `X-Demo-Sandbox` on every later call**: an offer and its proposal must be in the same session. A header-less call that names an `offerId` or `proposalId` (path, query, JSON body or MCP tool arguments) continues in the one agent session that holds it, if the Bearer key's buyer owns it, so generic HTTP helpers that never echo headers still complete offer → proposal → getProposal/audit; another buyer's key never resolves it (404). MCP sessions are keyed by `Mcp-Session-Id` (issued on `initialize`); sending that id as `X-Demo-Sandbox` continues the same session over REST. Browsers keep their httpOnly cookie session and never receive the header. Two clients with the same agent key never see each other's totals, proposals or suspension unless they send the same id. Agent sessions expire after 2 idle hours (browser sessions after 6).
- **Agent suspension (T111).** Valio's admin can pause all agent writes (`AGENTS_PAUSED`). An agent is also suspended on its own, **per agent key and session**: the key's buyer plus its private session (T116, below). Refusals in one session never suspend another session of the same key. Only policy refusals count: an approve/decide attempt (approval tools or approval fields such as `approvalToken`, or an instruction in the request text that the gateway ignored), a mandate write, or acting for another buyer (403). 401, 404 (unknown route, tool or resource), 409 and other 422 errors never count. The second counted refusal within 10 minutes carries a warning (REST header `X-Agent-Warning`; MCP `error.data.warning` or `result._meta.warning`, plus the header). The third suspends that session for **2 minutes**: calls return 403 `AGENT_SUSPENDED` with `details.suspendedUntil` (ISO time), and the message says when it lifts and that Valio can resume it at once on `/valio`. The suspension then lifts on its own. The activity feed (`GET /v0.1/admin/agent-activity`) labels key-only callers as the buyer of the key (`Agent for <organisation> (<User-Agent>)`); each `suspended` entry has `until`.

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
- **Peppol order export (T72):** `GET /v0.1/proposals/{proposalId}/peppol-order` returns a `CONFIRMED_SIMULATED` proposal as a Peppol BIS Order (UBL 2.1, `urn:fdc:peppol.eu:poacc:bis:ordering:3`): buyer, seller (Valio Aimo), requested delivery period, and per line the quantity in sales units (UN/ECE Rec 20 `KGM`, `LTR` or `H87`), net line amount, net unit price, VAT rate, the Valio product number and, when the product data has one, the GTIN (`schemeID="0160"`, from `Product.gtin`, optional). The document carries `<cbc:Note>Simulated order — mock environment</cbc:Note>` and is never sent anywhere. Read scope is the same as `GET /v0.1/proposals/{proposalId}`; a proposal that is not confirmed is `409`. `/spec` maps proposal states to Order Response codes: confirmed → `AP` (lines `5`), waiting for a trade-off sign-off or superseded → `CA` (changed lines `3`), rejected / blocked / expired → `RE` (lines `7`).
- **Published keys, discovery & offline verification (T67):** `GET /.well-known/aimo-connect-keys.json` publishes the gateway's Ed25519 public key as an RFC 8037 OKP JWK Set (`kty: "OKP"`, `crv: "Ed25519"`, `alg: "EdDSA"`, `kid`, `x`), and `GET /.well-known/aimo-connect.json` points to OpenAPI, MCP, `/spec`, and the JWK Set. Detached base64url signatures cover canonical UTF-8 JSON (`sort_keys=True`, `(",", ":")` separators) for `aimo-connect.offer.v0.1`, `aimo-connect.decision.v0.1`, `aimo-connect.confirmation.v0.1`, and `aimo-connect.audit-entry.v0.1`. `scripts/verify-audit <export.json> [--keys <jwks>]` verifies both the SHA-256 chain and Ed25519 signatures offline using the Python standard library alone.

## Trust labels and existing standards (T110)

Agent Trust Labels map to concepts in GS1 product data and W3C Verifiable Credentials (VC). They are not GS1 messages and not Verifiable Credentials, and labels are not signed. The same tables are on `/spec#standards`.

| Trust level | GS1 | W3C VC |
|---|---|---|
| `VERIFIED` | No GS1 trust level. Price and availability are trading-partner transaction data, not GDSN master data. | `issuer` = Valio Aimo's live price or availability system. |
| `DECLARED` | Supplier master data published through GDSN (GS1 Synkka in Finland); the brand owner is the information provider. | `issuer` = the supplier, read through `valio-aimo.product-data`. |
| `ESTIMATE` | No GS1 equivalent. | `issuer` = `derived` (the gateway) or a statistics source; `evidence` = `method`, always stated. |
| `UNKNOWN` | No GS1 equivalent. A missing allergen declaration is never read as `FREE_FROM`. | No credential exists for the fact. |

| Label field | GS1 | W3C VC |
|---|---|---|
| `fact` + `value` | Attribute and value, e.g. `allergen.AM` = allergen type code AM (milk) with a level of containment; `claim.FREE_FROM_GLUTEN` = nutritional claim type `FREE_FROM` for `GLUTEN`. | A claim in `credentialSubject` (property and value). |
| allergen status | `CONTAINS`, `MAY_CONTAIN` and `FREE_FROM` map to the GS1 `levelOfContainmentCode` values of the same name. `NOT_LISTED`, `UNDECLARED` and `UNKNOWN` describe missing or partial declarations and never prove absence. | The claim value. |
| `productId` | Valio Aimo's own product number. The GS1 identifier is the GTIN, which v0.1 does not carry. | `credentialSubject.id` (what the claim is about). |
| `source.system` | For `DECLARED` facts, the GDSN data source. | `issuer` |
| `source.field` | The attribute name in the source record. | The property name inside `credentialSubject`. |
| `source.retrievedAt` | No GS1 equivalent. | `issuanceDate` (`validFrom` in VC Data Model 2.0). It is when the gateway read the fact, not when the source first stated it; `null` for static data. |
| `method` | No GS1 equivalent. | `evidence`: how the value was derived or checked. |
| `level` | No GS1 equivalent. | No VC field: our classification of the issuer and method. |
| `conflict` | No GS1 equivalent. | No VC field: two sources whose claims disagree. |

## Reason-code registry

The canonical registry is `app.contracts.REASON_REGISTRY`; generated TypeScript exports the same copy. Each entry has `code`, default `effect`, `allowedEffects`, `approvable`, `chipText`, and `template`. Effects are `ASK | EXCLUDE | BLOCK | INFO | NO_ORDER`; `EXCLUDE` removes the candidate, not the entire viable order. `NO_ORDER` means no confirmable proposal: do not invent a confirmation or ask a human to waive missing evidence. T05 computes decisions and applies context, not the registry.

| Default effect | Codes | Approvable |
|---|---|---|
| ASK | `OVER_AUTO_LIMIT`, `DATE_LATER`, `PARTIAL_AVAILABILITY`, `SUPPLIER_STOCK_UNKNOWN`, `VALIO_SUBSTITUTE`, `AGENT_SUBSTITUTE`, `LOW_RELIABILITY`, `QUANTITY_BASIS_WEAK`, `INSTRUCTION_IGNORED`, `STALE_DATA` | yes |
| EXCLUDE | `CLAIM_MISSING`, `ALLERGEN_CONTAINS`, `ALLERGEN_MAY_CONTAIN`, `ALLERGEN_UNKNOWN`, `ALLERGEN_IMPLICIT`, `DATA_CONFLICT`, `CUTOFF_PASSED`, `UNIT_MISMATCH`, `NOT_AVAILABLE`, `SALES_BLOCKED`, `PRICE_UNAVAILABLE`, `SUBSTITUTE_REJECTED`, `DEPOSIT_COST_UNVERIFIED`, `QUANTITY_BASIS_UNKNOWN`, `CATEGORY_NOT_ALLOWED` | no |
| BLOCK | `OVER_HARD_LIMIT`, `MANDATE_NOT_VALID` | no |
| INFO | `SPLIT`, `REQUOTED`, `VARIABLE_WEIGHT_UNVERIFIED` | no |
| NO_ORDER | `NO_CANDIDATE`, `CLARIFICATION_NEEDED` | no |

Contextual effects are explicit: `VALIO_SUBSTITUTE` may be INFO when the mandate auto-accepts a compliant Valio substitute. `INSTRUCTION_IGNORED` (O4, T107) is ASK when the order would otherwise be AUTO or ASK: a request that tried to bypass approval, raise a limit or claim a prior sign-off is never ordered automatically, and the approver signs off with the chip "Instruction ignored" and the text "The request contained an instruction to bypass approval; a person checks this order." Under BLOCK or NO_ORDER it is INFO (context only), so Scenario C stays BLOCK `[OVER_HARD_LIMIT, INSTRUCTION_IGNORED]`. The attempt itself is never executed and the re-check keeps the reason, so an approved proposal confirms instead of being superseded. `STALE_DATA` (O7, T71): when Valio's price/availability API fails (timeout, error or a 429 cooldown, which is never retried inside its 30 s block), the gateway may answer from the last successful read of the same products for the same customer and date, at most 15 minutes old. Every such price and stock fact is `ESTIMATE` with `method` "stale fallback · Valio API, cached at HH:MM" and `source.retrievedAt` the original read time. Such an order is never AUTO. A person may approve it, and the approval re-checks live before confirmation; if the API is still down, nothing is confirmed. Without a fresh copy the original upstream error is returned. `ALLERGEN_CONTAINS` may BLOCK a specifically named product forbidden by the mandate. None of these exceptions lets a human waive a hard requirement. Partial availability is ASK **only for a separately versioned, explicitly partial proposal**; supplier stock only with an open cut-off; every substitute must prove every hard requirement.

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
offer, proposal, explanation and reasons (often tens of KB). Existing SSE/NDJSON streams are unchanged.

**External agents should use `?view=compact`.** It runs the same agent and deterministic gateway, then
returns only a decision summary (`CompactAgentRunResult`, a few KB; Scenario B is under 2 KB). OpenAPI
documents the `view` parameter, the schema and two response examples (`compact_ask`,
`compact_no_candidate`), and the `createProposal`/`getProposal` descriptions point here.

```json
{
  "summary": "ASK: €506.16 excl. VAT, 2 lines, waits for Head Chef approval. €506.16 excl. VAT is above your €500.00 auto-approve limit.",
  "customerId": "northbay-school-kitchen",
  "mode": "RECORDED",
  "recordedAt": "2026-11-13T21:52:02+02:00",
  "proposalId": "PR-EXAMPLE-v1",
  "decision": "ASK",
  "state": "PENDING_APPROVAL",
  "reasons": ["OVER_AUTO_LIMIT"],
  "reasonTexts": [
    {"code": "OVER_AUTO_LIMIT", "effect": "ASK", "text": "€506.16 excl. VAT is above your €500.00 auto-approve limit."}
  ],
  "totals": {"exclVat": "506.16", "vat": "68.33", "inclVat": "574.49", "currency": "EUR"},
  "lines": [
    {"productId": "MOCK-PASTA", "name": "Synthetic gluten-free macaroni", "quantitySalesUnits": "18", "salesUnit": "ST", "unitPriceExclVat": "22.92", "lineTotalExclVat": "412.56"},
    {"productId": "MOCK-OAT", "name": "Synthetic gluten-free oat drink", "quantitySalesUnits": "80", "salesUnit": "ST", "unitPriceExclVat": "1.17", "lineTotalExclVat": "93.60"}
  ],
  "approverRole": "Head Chef",
  "clarificationQuestion": null,
  "noOrder": null,
  "links": {"proposal": "/v0.1/proposals/PR-EXAMPLE-v1", "audit": "/v0.1/audit?proposalId=PR-EXAMPLE-v1"},
  "notOrdered": []
}
```

The example is synthetic. Quantities count **sales units**, with unit prices excl. VAT; decimals
remain strings. `decision`, `state`, `reasons`, totals and lines come from the final proposal, not
from a model-generated summary. `mode` and the snapshot's `recordedAt` preserve data provenance
(`recordedAt` is null when no offer/proposal snapshot exists). Relative links point only to public
agent-channel reads; follow them in the same session to retrieve the full proposal and audit.
No facts, candidate lists, approval tokens, approval links or QR payloads are included.

- `summary` is one deterministic line (never model-written): the outcome, the total, who must approve
  and the decisive reason sentence(s); for no order, what is missing and whether a later date works.
- `reasonTexts` lists every code in `reasons`, in the same order, as `{code, effect, text}`. `text` is the
  sentence the buyer UI shows for that code with the real values (limit, total, dates, the same-day
  committed total when that is what crossed the limit, the clarification question). `reasons` stays a
  plain code list for existing clients.

`notOrdered` (T106) lists requested lines the proposal leaves out because no compliant product was
found or every candidate failed at the offer stage (cut-off passed, no stock, too late, requirement not declared), e.g. `{"itemId": "I2", "what": "oat drink", "quantity": "30", "unit": "l", "reason": "no gluten-free
oat drink was found"}`. It mirrors `proposal.notOrdered` in the full view. When a multi-line request has at
least one deliverable line and at least one line with no compliant candidate, the agent proposes the
deliverable lines on their own; the gateway decides that version from scratch with `PARTIAL_AVAILABILITY`
(never AUTO), and `reasonDetails.PARTIAL_AVAILABILITY` and the approval statement start with one plain
sentence: "Approving means pasta only; oat drink is not ordered: no gluten-free oat drink was found." The
not-ordered lines are never ordered. If no line is deliverable, the result is the usual no-order result.
`createProposal` (REST and MCP) accepts the same optional `notOrdered` list; it can only make a decision
stricter.

If no proposal is created (clarification or no candidate), `proposalId`, `decision`, `state`,
`totals`, `approverRole` and `links` are null, and `lines` is empty. `reasons`, `reasonTexts` and any
`clarificationQuestion` explain what is missing; no policy outcome or zero-priced order is invented.
For NO_CANDIDATE, `noOrder` carries the same data the UI's no-order banner shows:

```json
"noOrder": {
  "items": [{"itemId": "I1", "searched": "milk · lactose-free",
             "why": "Available Mon 16 Nov, after your Fri 13 Nov deadline. Exclusions include cut-off passed.",
             "closestMatches": []}],
  "earliestDeliverableDate": "2026-11-16",
  "retry": {
    "hint": "The full request can be delivered on Mon 16 Nov. Send the same requestText with lookupDate 2026-11-16 to get a proposal for that day; the later date needs approval.",
    "method": "POST", "path": "/v0.1/agent/run?view=compact",
    "body": {"customerId": "nordic-harbour-hotels", "requestText": "I need 10 litres of lactose-free milk today.", "lookupDate": "2026-11-16"}
  }
}
```

`closestMatches` (at most 3 per item) pass the requested product-type check and every hard requirement
(T105); they are suggestions, never ordered. Each may carry `lacks`, a plain phrase saying what keeps it out
(T113): requested words the name does not state (`"fresh, skin-on not stated"`, when such a modifier zeroed
the search) or, when same-type candidates exist but all were excluded, the exclusion reason
(`"order cut-off passed for Mon 16 Nov"`, `"not declared gluten-free"`, `"earliest delivery Mon 16 Nov"`). `earliestDeliverableDate` is set only when a quote confirms
the full request on that day; `retry` is then a ready request body. The NO_CANDIDATE reason text names
the searches, the closest matches and the earliest date, so it is readable on its own.

Compact view is non-streaming JSON only. Combining it with `stream=true` (query or body),
`Accept: text/event-stream` or `Accept: application/x-ndjson` returns HTTP 422 before any agent
work. An unsupported `view` value also returns 422. Other validation and upstream errors retain
the existing status codes and error shape.

## Retrying a delivery date

`POST /v0.1/agent/run` can return `steps[].noOrder` with the search, outcome, and optional catalogue matches for each item (in the compact view: `noOrder`, see above). If availability supports the full request on a later day, `earliestDelivery` (compact: `earliestDeliverableDate`) is an ISO date. The buyer can rerun with the original `requestText` and that date as `lookupDate`.

Agents that call `createProposal` directly (REST `POST /v0.1/proposals` or the MCP tool) get the same help on a NO_CANDIDATE refusal: the `NO_ORDER` error's `details.noOrder` has `items` (`itemId`, `searched`, `why`, `closestMatches`), `earliestDeliverableDate` and `retryHint` (call `getOffer` again with that `lookupDate`, then `createProposal`). `details.reasons` and `details.excluded` are unchanged.

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
