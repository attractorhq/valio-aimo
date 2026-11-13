# Tickets

After bootstrap, the source of truth is the GitHub issues labelled `ticket` in `attractorhq/valio-aimo`. Run `scripts/ticket status` for the live state.

This file is the **seed**. `scripts/ticket-bootstrap` creates one issue per `### T..` section:
- dependencies are rewritten from `T..` to `#N`;
- labels come from the `Lane:` / `Priority:` line.

References:
- `§x` points to the brief: `docs/brief/brief.txt` (sanitised; currently §0–§8.6).
- `Px` points to Patch v2: `docs/brief/patch-v2.txt`. **The patch wins on conflicts.**

**Priorities follow patch P6 and AC18: P0 must be complete and polished before any P1 or P2 work starts.**
- `scripts/ticket` enforces this. P1 cannot be claimed while any P0 is open, and P2 waits for every P0 and P1.
- The architect can override a single ticket with the label `unlocked`.

**Milestone M1 (first end-to-end slice, then the first judge run):**
- core path: T01 → T02, T03, T05 → T06 → T07 → T08 → T09 → T10;
- UI: T11, T12, T13;
- deployed via T17.

| # | Ticket | Lane | Prio | Depends on |
|---|---|---|---|---|
| T01 | Contracts v0.1: proposal flow, approvability, schemas | core | P0 | – |
| T02 | Catalogue build: normalised, trust-labelled product facts | data | P0 | T01 |
| T03 | Valio Aimo client, live facts, recorded mode, fixture recorder | data | P0 | T01 |
| T04 | searchProducts: FI/EN/SV search, hard requirements, excluded list | core | P0 | T02 |
| T05 | Policy engine: mandates, rules, approvable vs not, decision | core | P0 | T01 |
| T06 | getOffer: sizing, allocation, totals, offer version + hash | core | P0 | T02, T03, T05 |
| T07 | Proposals: state machine, recheckAndConfirm, approvals, decision log | core | P0 | T06 |
| T08 | REST API /v0.1: agent channel + approver channel, remove legacy | api | P0 | T04, T07 |
| T09 | Agent: request parser (LLM + deterministic fallback) | agent | P0 | T01 |
| T10 | Agent: planner, ranking, Valio-substitute check, explanation | agent | P0 | T08, T09 |
| T11 | Web: main view, four-step flow, mandates | web | P0 | T01 |
| T12 | Web: trust badges, excluded list, decision footer | web | P0 | T11 |
| T13 | Web: separate Approver panel | web | P0 | T11 |
| T14 | Web: decision log per proposal | web | P0 | T11 |
| T15 | Openness minimum: OpenAPI + second client with identical decision | api | P0 | T08 |
| T16 | Evaluation on fixtures (24 rows) + live pre-flight + recorded mode | agent | P0 | T03, T10 |
| T17 | Deploy: Cloudflare Worker + Container, one origin | infra | P0 | T08 |
| T18 | Demo kit + end-to-end browser test | demo | P0 | T10, T12, T13, T17 |
| T19 | Submission text, README and pitch | demo | P0 | T18 |
| T20 | Phone approval via QR (single-use demo link) | web | P1 | T13 |
| T21 | Mandate controls that re-decide live | web | P1 | T05, T11 |
| T22 | Good / Better / Best alternatives | agent | P1 | T10, T12 |
| T23 | Scenario B2: partial-delivery proposal versions | core | P1 | T06, T10 |
| T24 | Editable assumption chips | web | P1 | T10, T11 |
| T25 | Finnish input polish | agent | P1 | T09 |
| T26 | MCP agent interface | api | P2 | T08 |
| T27 | Valio Aimo readiness view | web | P2 | T02, T11 |
| T28 | Hash-chain tamper evidence | core | P2 | T07 |
| T29 | Open-spec page (Agent Trust Labels v0.1) | web | P2 | T08 |
| T30 | Audit export | api | P2 | T07 |

**Parallel start:** T01. Once its schemas are merged, T02, T03, T05, T09 and T11 can run in parallel.

## Starting point: live Valio integration in commit 0e74e98

Commit 0e74e98 ("api integration working") already contains tested work. **Reuse it; do not rewrite it from scratch.**

| Existing code | Reused in | Keep | Must change (brief/patch wins) |
|---|---|---|---|
| `backend/app/integrations/real_valio.py` + `provider.py` | T03 → `backend/app/valio/` | Typed Pydantic vendor models; Decimal parsing; customer/currency/date/duplicate-id validation; no redirects with the key; price validity window; Terminal 999999 = unknown; cut-off vs `timeNow`; sales blocks; customer binding | (1) **Per-product facts, not whole-batch failure**: one invalid price or schedule must exclude that line (L1–L3), not fail the whole offer. (2) Read `tax.taxRate`, `comparisonPrice`, `listPrice`, `netPriceFound`; VAT is required (§5.2). (3) A later date is a DATE_LATER fact (approvable), not "unavailable" (P2.2). (4) Terminal with an open cut-off = SUPPLIER_STOCK_UNKNOWN (approvable), not a hard reject (P2.2). (5) Output TrustLabels. (6) TTL cache + recorded mode (P5). |
| `backend/tests/test_live_provider.py` | T03 | httpx `MockTransport` contract tests on **synthetic** records (P7-compliant): validity, negative prices, 401/403/429/500, blocks, cut-offs, Terminal | Adapt to the per-product fact model |
| `backend/app/integrations/valio_catalog.py` | T02 → `backend/app/catalog/normalise.py` | Real field mapping; claims → dietary; `LOW_LACTOSE` ≠ lactose-free; pack method 1 (unitConversions × units); substitution codes; deposits; variable weight. GS1 code map **fixed** (ML = lactose, UM = molluscs, AW = gluten cereals, UW = wheat) with a regression test. | (1) Gluten-free is a **claim** rule: GO (oats) CONTAINS + FREE_FROM_GLUTEN is OK (§6.3). Today "oats ⇒ gluten" would exclude Scenario B's 468465. (2) X99 per P4. (3) Pack methods 2 and 3 as ESTIMATE → QUANTITY_BASIS_WEAK (§5.2). (4) Implicit "not listed" is OK in STANDARD (P2.4). |
| `services/policy.py` additions | T05 | New findings: **deposits** (506 products carry unpriced returnable deposits) and **variable weight** (632 products) | Deposits: the brief is silent, so decide in T01 (proposed: not approvable, "total incomplete"). Variable weight: brief §5.2 says 1 kg per sales unit, so follow the brief. Allergen "unknown → escalate (ASK)" becomes EXCLUDE (P2.3). |
| `scripts/download_valio_data.py` | README (T19), WORKFLOW | Fetches the data with your own event access (P7) | — |
| `frontend/e2e-live/` + `playwright.live.config.ts` | T18 | Opt-in live browser check pattern | Retarget to scenarios A, B and C |

---

### T01: Contracts v0.1: proposal flow, approvability, schemas
Lane: core · Priority: P0 · Depends on: –
Touches: backend/app/contracts/**, spec/schemas/**, docs/API.md, frontend/src/lib/contracts.ts, frontend/src/mocks/**, backend/tests/test_contracts.py

**Goal.** Lock the shared contracts first, so data, core, agent and web can work in parallel.

**Scope.**
- Pydantic v2 models:
  - TrustLabel (§5, §8.4);
  - Product (normalised);
  - StructuredRequest (§7.2);
  - Offer with `offerId`, `offerVersion`, `offerHash` and `validUntil` (P1.5);
  - Proposal with the states of P1.4;
  - Decision;
  - Mandate with a version (§6.1);
  - AuditEntry;
  - **ApproverView**: the approver channel only, the only model that carries the approval token.
- Agent-facing models never contain an approval URL, token or QR field (P3, AC15).
- Reason-code registry (§6.9 + P2). Also decide the two codes found in 0e74e98: returnable deposits that the price API does not price, and variable weight, which follows §5.2 (1 kg per sales unit):
  - code → effect, `approvable` (true/false), chip text, template;
  - the CUTOFF_PASSED template from P2.6;
  - formatters for `€1,010.00` and `Mon 16 Nov`.
- JSON Schema export to `spec/schemas/*.json` (v0.1).
- `docs/API.md` describes the P1.5 operations and the P1.4 state machine.
- TypeScript types and mocks in the shape of Scenario A and Scenario B (P8).

**Acceptance.**
- The Scenario B example (P8) validates.
- The registry marks every P2.3 reason as not approvable.
- A test asserts that no agent-facing schema has a token, URL or QR field.
- No business logic.

### T02: Catalogue build: normalised, trust-labelled product facts
Lane: data · Priority: P0 · Depends on: T01
Touches: backend/app/catalog/normalise.py, backend/app/catalog/build.py, backend/app/catalog/categories.py, backend/app/trust/**, backend/app/integrations/valio_catalog.py, scripts/build-catalog, scripts/import_products.py, backend/tests/test_catalog_*.py, backend/tests/synthetic/**

**Goal.** Turn `data/raw/products.json` and the two CSVs into a compact catalogue artifact in `data/catalog/` (gitignored), with TrustLabels per §5.2.

**Start from** `backend/app/integrations/valio_catalog.py` (commit 0e74e98). Apply the changes listed in "Starting point" above.

**Scope.**
- Names and texts in en/fi/sv.
- Allergens:
  - CONTAINS / MAY_CONTAIN / UNDECLARED / FREE_FROM / implicit / UNKNOWN;
  - **X99 per P4**: proves free-from only for the EU-14 codes and their subcodes; every other code is UNKNOWN.
- Claims, labels and origin.
- Content per sales unit:
  - method 1 (DECLARED) from unit conversions;
  - method 3 from the name (ESTIMATE);
  - a hook for method 2 (price ratio);
  - KG and variableUnit products count as 1 kg;
  - K5 when methods disagree.
- K1 and K2 conflicts.
- Reliability and CO2 (ESTIMATE; missing values are UNKNOWN).
- Valio substitution relations.
- Food/non-food classification and per-category profiles for product-type matching.
- `scripts/build-catalog` prints the recomputed dataset statistics.

**Acceptance.**
- Synthetic unit tests cover every allergen and X99 rule. Synthetic means hand-made records, not copied from the dataset.
- On the real data (local), the build prints the statistics and they match `docs/PLAN.md` §1, or the difference is noted.
- Build under 60 s; the artifact loads in under 2 s.

### T03: Valio Aimo client, live facts, recorded mode, fixture recorder
Lane: data · Priority: P0 · Depends on: T01
Touches: backend/app/valio/**, backend/app/integrations/{real_valio,provider}.py, scripts/record-fixtures, backend/tests/test_valio_*.py, backend/tests/test_live_provider.py, backend/tests/synthetic/**

**Goal.** A safe client for `/api/price` and `/api/availability` with **live** and **recorded** modes (P5), plus conversion into TrustLabels.

**Start from** `RealValioProvider` and its MockTransport tests (commit 0e74e98). Keep the validation and apply the six changes listed in "Starting point" above.

**Scope.**
- **Client:**
  - httpx; the key is read from env only;
  - timeout;
  - at most 20 ids per item, hard cap 1,000;
  - 30–60 s TTL cache;
  - backoff on 429/5xx;
  - never logs the key.
- **Live facts:**
  - price validity window;
  - "customer price" when `netPriceFound`;
  - stock tiers: exact / plenty / Terminal 999999 = UNKNOWN / out / not deliverable;
  - delivery date;
  - cut-off vs `timeNow` (K3);
  - salesBlock;
  - unit mismatch (K4).
- **Recorded mode:**
  - provider = files in `data/fixtures/` (gitignored, P7);
  - "now" frozen to the recorded `timeNow`;
  - the UI and API expose `mode: live | recorded` and the capture time.
- **`scripts/record-fixtures`** records raw responses for every product id the brief and patch mention, among them 464306, 465039, 468465, 469040, 470699, 470863, 471108, 471881, 473895, 474136, 474235, 475795, 460302 and 460825, plus the shortlist ids the scenarios touch.

**Acceptance.**
- Unit tests on synthetic responses cover every stock tier, K3, K4, price outside validity, an unknown id and an empty schedule.
- The recorder writes `data/fixtures/<capture-ts>/`.
- No committed test needs the network or the key.

### T04: searchProducts: FI/EN/SV search, hard requirements, excluded list
Lane: core · Priority: P0 · Depends on: T02
Touches: backend/app/catalog/search.py, backend/app/catalog/vocab.py, backend/tests/test_search*.py

**Goal.** Static-only lookup (§7.3): candidates + `excluded[]` with reason codes.

**Scope.**
- FI↔EN↔SV vocabulary.
- Substring matching for Finnish compounds.
- Product-type resolution via category profiles; non-food is excluded from food requests.
- **Hard requirements per P2.4:**
  - proven free = explicit FREE_FROM, or X99 for EU-14, or (STANDARD only) declared and not listed;
  - everything else is EXCLUDED;
  - a required claim must be declared (CLAIM_MISSING otherwise);
  - lactose-free is proven only by FREE_FROM_LACTOSE;
  - milk-free ≠ lactose-free;
  - STRICT requires a declaration.

**Acceptance.**
- Synthetic tests for every P2.4 branch.
- Local fixture tests:
  - "gluten-free oat drink" excludes 460825 and 470863 (CLAIM_MISSING) and 471881 (DATA_CONFLICT);
  - in STRICT, 470699 and 460302 are ALLERGEN_UNKNOWN.
- Under 100 ms per query.

### T05: Policy engine: mandates, rules, approvable vs not, decision
Lane: core · Priority: P0 · Depends on: T01
Touches: backend/app/policy/**, backend/tests/test_policy_*.py

**Goal.** Deterministic, pure policy (§6 as amended by P2). This is the only place where AUTO / ASK / BLOCK is computed.

**Scope.**
- Mandates M1 and M2 (§6.2): versioned; read-only for agents; a change is an audit event.
- Line rules L1–L14 and order rules O1–O6, with effects per P2:
  - **ASK only for approvable reasons** (P2.2);
  - non-approvable line reasons → EXCLUDE (P2.3);
  - hard limit or forbidden allergen in a named product → BLOCK.
- Decision per §6.6.
- **No "approve verified lines only"** (P2.5).

**Acceptance.**
- One unit test per rule, covering both sides of each threshold.
- A property test: no decision is ever ASK with a non-approvable reason (AC16).
- 100 % branch coverage on `policy/`.

### T06: getOffer: sizing, allocation, totals, offer version + hash
Lane: core · Priority: P0 · Depends on: T02, T03, T05
Touches: backend/app/gateway/offers.py, backend/tests/test_offers.py

**Goal.** A live (or recorded) offer for the shortlist (§7.4–7.7, P1.2, P1.5).

**Scope.**
- One batched availability call and one batched price call per evaluation.
- Sizing: ceil, allowedLotSize, at most one sales unit of over-delivery.
- Allocation across candidates.
- Totals in Decimal excl. VAT, with VAT from the API.
- `offerVersion` + `offerHash` over lines, unit prices, VAT, quantities, dates, cut-offs and trust labels.
- `validUntil` = min(+15 min, price `validTo`).

**Acceptance.**
- **Fixture tests:**
  - Scenario A: 1,000 × €1.01 = €1,010.00.
  - Scenario B: 474235 × 18 = €412.56 and 468465 × 80 = €93.60; total €506.16, VAT €68.33, incl. VAT €574.49.
- The hash is stable across runs and changes when the price changes.
- Nothing is hardcoded.

### T07: Proposals: state machine, recheckAndConfirm, approvals, decision log
Lane: core · Priority: P0 · Depends on: T06
Touches: backend/app/gateway/proposals.py, backend/app/gateway/approvals.py, backend/app/gateway/log.py, backend/app/db/**, backend/tests/test_proposals*.py

**Goal.** A proposal comes before confirmation (P1). Nothing is confirmed without a server-side authority check and a re-check.

**Scope.**
- `createProposal` binds customerId + mandateVersion + request + offerId/offerVersion/offerHash + totals (P1.2).
- The state machine of P1.4. Every transition appends a decision-log entry: what happened, who or what decided, which rule, which facts.
- **`recheckAndConfirm` (internal):**
  - re-fetches live data for the proposal lines;
  - checks mandate, price, quantity, delivery (date, cut-off, block) and hard requirements;
  - on any difference → SUPERSEDED, a new offer and proposal version decided from scratch, and the old approval is void.
- **Approvals:**
  - a single-use token bound to proposalId + offerVersion + offerHash that expires with the offer;
  - APPROVE / REJECT only;
  - BLOCK is never approvable;
  - audit actor "human:Head Chef (demo, link-based)".
- Idempotent create.

**Acceptance.**
- Tests for every transition, token reuse, an expired offer → EXPIRED, and approving BLOCK being refused.
- **Eval row 23:** after a +€0.01 price change before the re-check → SUPERSEDED, the approval is void, and a new v2 is decided.

### T08: REST API /v0.1: agent channel + approver channel, remove legacy
Lane: api · Priority: P0 · Depends on: T04, T07
Touches: backend/app/main.py, backend/app/api/**, backend/app/{schemas,auth}.py, backend/app/services/**, backend/app/integrations/**, backend/app/agent/{prompt,runner,tools}.py, backend/tests/**, scripts/import_products.py, README.md

**Goal.** Expose the P1.5 operations, and remove the old prototype's domain code.

**Keep:** the live adapter (moved to `valio/` in T03), `scripts/download_valio_data.py` and the customer-binding settings.

The prototype code to remove: DEMO fixtures, the mock provider, the chat endpoint, the old proposal/policy schemas and the bearer demo identities.

**Scope.**
- **Agent channel:** `searchProducts`, `getOffer`, `createProposal`, `getProposal`, `getAuditTrail`, `getMandate`.
- **Approver channel** `/v0.1/approver/*`:
  - view a pending proposal by token;
  - `decideApproval`;
  - never linked from agent responses.
- Mandate display endpoint.
- Error model.
- `/health`.
- OpenAPI 3.1 with clear tags.

**Acceptance.**
- A curl walkthrough of A (AUTO → CONFIRMED_SIMULATED), B (ASK → approver APPROVE → RECHECKED → CONFIRMED_SIMULATED) and C (BLOCK).
- A test scans every agent-channel response for URLs, tokens and QR payloads (AC15).
- The old tests are removed or rewritten.

### T09: Agent: request parser (LLM + deterministic fallback)
Lane: agent · Priority: P0 · Depends on: T01
Touches: backend/app/agent/parse.py, backend/app/agent/injection.py, backend/app/agent/llm.py, backend/tests/test_parse*.py

**Goal.** Buyer text in English or Finnish → StructuredRequest (§7.2).

**Scope.**
- LLM: **GLM via OpenRouter** (`OPENROUTER_API_KEY`, `LLM_MODEL`; OpenAI-compatible, so reuse the existing `openai` SDK client with `base_url=https://openrouter.ai/api/v1`). Use tool calling or a JSON schema, and validate the result with the StructuredRequest model. Anything invalid goes to the fallback.
- **Deterministic fallback:**
  - regex + vocabulary for quantities, units, requirements and dates;
  - "by Monday" = the next Monday, or today if today is Monday; Europe/Helsinki.
  - The core Finnish vocabulary of §7.2 is required. Wider Finnish polish is T25.
- **O4 injection detector:** deterministic; always logs `INSTRUCTION_IGNORED`.
- **O6:** exactly one clarifying question when needed.
- Timeout + fallback in under 3 s.

**Acceptance.**
- Parser tests for the A, B, C and B2 texts in English, plus Finnish variants of A and B.
- The fallback alone handles A, B and C.

### T10: Agent: planner, ranking, Valio-substitute check, explanation
Lane: agent · Priority: P0 · Depends on: T08, T09
Touches: backend/app/agent/plan.py, backend/app/agent/explain.py, backend/app/agent/run.py, backend/app/api/agent_routes.py, backend/tests/test_agent_*.py

**Goal.** A demo purchasing agent that uses **only the public agent channel** (P1.6, §7.12).

**Scope.**
- Shortlist of ≤ 20 per item → getOffer → createProposal → getProposal.
- Ranking per §7.6.
- **Valio-listed substitutes are re-checked against every hard requirement.** A substitute that fails → SUBSTITUTE_REJECTED, shown on the trust card and in Excluded (P8 B).
- Agent-proposed substitutes → ASK · AGENT_SUBSTITUTE, only if every requirement is proven.
- An explanation of ≤ 2 sentences (LLM, with a template fallback).
- `POST /v0.1/agent/run` streams the four steps for the UI.
- **The agent never approves, confirms or computes the decision.**

**Acceptance.** On fixtures:
- A = AUTO;
- B = ASK with exactly `[OVER_AUTO_LIMIT]`, €506.16, and 470863 → SUBSTITUTE_REJECTED;
- C = BLOCK + INSTRUCTION_IGNORED.

End-to-end in under ~5 s.

### T11: Web: main view, four-step flow, mandates
Lane: web · Priority: P0 · Depends on: T01
Touches: frontend/src/App.tsx, frontend/src/pages/Main.tsx, frontend/src/components/flow/**, frontend/src/lib/**, frontend/src/styles.css, frontend/src/mocks/**, frontend/src/pages/{Buyer,Approvals,History,Settings}.tsx, frontend/src/components/Chat.tsx

**Goal.** ONE main view (P6 P0-1, P10 S1). It replaces the chat-based pages.

**Scope.**
- Mandate picker (M1/M2) with the mandate shown read-only: limits, allergen mode, approver.
- Request input + scenario buttons A, B and C.
- The structured request card (read-only in P0; editable chips come in T24).
- **Step labels, exactly:**
  1. REQUEST
  2. LOOKUP
  3. ORDER: "Proposal at fixed price · offer v1 · €… excl. VAT"
  4. APPROVAL: "Agent decided → Confirmed (simulated)", "Waiting for approver" or "Blocked by mandate"
- Live/recorded mode badge.
- Works on mocks until T10 lands.
- Responsive.

**Acceptance.**
- Playwright smoke test on mocks.
- Accessible; colour is never the only signal.

### T12: Web: trust badges, excluded list, decision footer
Lane: web · Priority: P0 · Depends on: T11
Touches: frontend/src/components/trust/**, frontend/src/components/order/**, frontend/src/pages/Main.tsx

**Goal.** Make trust and precision visible (P6 P0-2, P0-3; P10 S2).

**Scope.**
- Proposal lines with trust badges (icon + word + source + time) on the decisive facts:
  - price;
  - stock;
  - delivery date and cut-off;
  - each hard requirement.
- UNKNOWN is shown as "Unknown".
- The Excluded list with reason chips, including the Valio-substitute line, which also appears on the line's trust card.
- Decision footer with the reason chips and the explanation.
- **ASK shows "Waiting for Head Chef (demo)" with NO approve button.**
- "Simulated order — mock environment".

**Acceptance.** Scenario B renders exactly as in P8: one reason, the excluded entries, and the substitute check.

### T13: Web: separate Approver panel
Lane: web · Priority: P0 · Depends on: T11
Touches: frontend/src/pages/Approver.tsx, frontend/src/components/approver/**

**Goal.** An approver that is visibly not the agent (P6 P0-4, P3, P10).

**Scope.**
- Route `/approver`, a separate view: "Approver — Head Chef (demo)".
- The pending proposals, read via the approver channel.
- The proposal view shows:
  - "You are approving version 1 of this proposal";
  - the exact lines, quantities, date and total;
  - the single reason in plain language;
  - the buttons "Approve €506.16" and "Reject";
  - the label "Demo approval link — single use".
- After approval: "Approved · re-checked live · Confirmed (simulated) · logged HH:MM".
- SUPERSEDED, EXPIRED and used-token states.

**Acceptance.** Playwright runs B: main view ASK → Approver panel → Approve → CONFIRMED_SIMULATED shown in both views.

### T14: Web: decision log per proposal
Lane: web · Priority: P0 · Depends on: T11
Touches: frontend/src/pages/Log.tsx, frontend/src/components/log/**

**Goal.** A readable decision log per proposal for buyer and Valio Aimo (P6 P0-5, I4).

**Scope.**
- A timeline in which every entry shows:
  - what happened;
  - who or what decided (agent / gateway rule / human);
  - which rule (reason code + template);
  - which facts, with their trust labels.
- SUPERSEDED and INSTRUCTION_IGNORED entries are visible.
- Linked from the main view and the Approver panel.

**Acceptance.** For A, B and C, the full chain from request to the final state is readable without knowing the code.

### T15: Openness minimum: OpenAPI + second client with identical decision
Lane: api · Priority: P0 · Depends on: T08
Touches: scripts/second-client, docs/API.md, README.md

**Goal.** "Here's the OpenAPI spec. Here's a second client getting the exact same decision." (P6 P0-8, P12)

**Scope.**
- `scripts/second-client` is a standalone Python script (stdlib + `urllib`, no repo imports). It reads `/openapi.json` and runs Scenario B through the agent channel only.
- It prints: decision, reasons, totals, offerHash.
- A curl version is documented in `docs/API.md`.

**Acceptance.**
- Against the same offer, its decision and reasons equal the UI agent's.
- It runs against the deployed URL.

### T16: Evaluation on fixtures (24 rows) + live pre-flight + recorded mode
Lane: agent · Priority: P0 · Depends on: T03, T10
Touches: backend/app/eval/**, scripts/eval, scripts/preflight, frontend/src/pages/Eval.tsx, frontend/src/components/flow/ModeBadge.tsx

**Goal.** Repeatable proof plus an honest live check (P5, P9, AC10).

**Scope.**
- Eval rows 1–24: brief §13 with rows 4 and 8 replaced and rows 21–24 added from P9. Stored as data: request, mandate, expected products, quantities, totals, reasons.
- `scripts/eval` runs them on `data/fixtures` with frozen "now" and reports "N/24" for README and UI.
- **`scripts/preflight` (and a UI button):**
  - runs A, B, C and B2 live;
  - prints scenario · expected · live · diffs (price, stock, date, cut-off);
  - green → present live; red → switch to recorded mode, labelled with the capture time.

**Acceptance.**
- `scripts/eval` passes all P0-relevant rows. Rows for P1 features are marked pending until those land.
- The pre-flight runs in under 30 s.

### T17: Deploy: Cloudflare Worker + Container, one origin
Lane: infra · Priority: P0 · Depends on: T08
Touches: edge/**, backend/Dockerfile, compose.yaml, .dockerignore, scripts/deploy, frontend/wrangler.toml (remove), frontend/public/_redirects, docs/WORKFLOW.md

**Goal.** A public URL for the demo, the approver and the blind judge, all on one origin.

**Scope.**
- **Worker** `edge/` (TypeScript, `@cloudflare/containers`):
  - serves `frontend/dist` as static assets with SPA fallback;
  - forwards `/v0.1/*`, `/mcp`, `/health`, `/docs` and `/openapi.json` to one container (`standard-1`, `max_instances: 1`, long `sleepAfter`).
- **Container** `backend/Dockerfile`:
  - FastAPI;
  - catalogue artifact and fixtures copied in at build time from the architect's `data/` (never committed);
  - no persistence: state resets on restart/deploy (accepted, see `docs/WORKFLOW.md` → Decisions; the README says so).
- Worker secrets → container env.
- Basic rate limit on `/v0.1/agent/*`.
- **`scripts/deploy`** (architect only): build-catalog → frontend build → `wrangler deploy`.
- `docker compose up` runs the same image locally.
- Remove the old Pages config.

**Acceptance.**
- `/health` OK on `https://valio-aimo.<account>.workers.dev`.
- A, B and C run there.
- `scripts/judge --selftest <url>` passes.

The developer verifies with `docker compose` and `wrangler dev`. The architect runs the first `wrangler deploy` from "Architect runs:".

### T18: Demo kit + end-to-end browser test
Lane: demo · Priority: P0 · Depends on: T10, T12, T13, T17
Touches: frontend/e2e/**, scripts/demo, docs/DEMO.md

**Goal.** A reliable live demo in the order A (AUTO) → B (ASK; the approver approves v1) → C (BLOCK) (P12).

**Scope.**
- Scenario buttons and a demo-state reset.
- Playwright e2e for A, B and C against the deployed URL. Reuse the opt-in live setup from `frontend/e2e-live/` and `playwright.live.config.ts`.
- `docs/DEMO.md`: the script with timings, the P12 lines, and a fallback to recorded mode.

**Acceptance.** The e2e test is green 3× in a row against the deployed URL. The pre-flight is green, or the recorded mode is shown honestly.

### T19: Submission text, README and pitch
Lane: demo · Priority: P0 · Depends on: T18
Touches: docs/SUBMISSION.md, README.md, docs/PITCH.md

**Goal.** What the jury reads and hears (§16 + P12).

**Scope.**
- `docs/SUBMISSION.md` final: claims only about what is deployed.
- **README:**
  - quick start;
  - how to fetch the data with your own event access (P7);
  - architecture;
  - "Assumptions & observed behaviour";
  - eval "N/24";
  - production path (SSO approver, persistence, MCP).
- **Pitch** mapped to the 5 judging criteria, using the P12 lines.

**Acceptance.** The architect's final judge run uses this exact submission text.

### T20: Phone approval via QR (single-use demo link)
Lane: web · Priority: P1 · Depends on: T13
Touches: frontend/src/pages/ApproveMobile.tsx, frontend/src/components/approver/QrCode.tsx

**Goal.** A judge approves on their own phone (P3, P10 S3).

**Scope.**
- A QR code **only in the Approver panel**.
- `/approve/:token` mobile page with the same content as the panel.
- Labelled "Demo approval link — single use".

**Acceptance.**
- Playwright runs at 390 px.
- A real phone on mobile data approves Scenario B.

### T21: Mandate controls that re-decide live
Lane: web · Priority: P1 · Depends on: T05, T11
Touches: frontend/src/components/mandate/**, backend/app/api/mandate_routes.py

**Goal.** D5: buyer-admin toggles (auto-approve limit, allergen mode) re-decide live with no model call.

Each change is audited as "Policy changed by buyer admin" and bumps `mandateVersion`, which supersedes open proposals.

**Acceptance.** Raising M2's limit to €510 turns Scenario B into AUTO. The log shows the policy change.

### T22: Good / Better / Best alternatives
Lane: agent · Priority: P1 · Depends on: T10, T12
Touches: backend/app/agent/plan.py, frontend/src/components/order/Alternatives.tsx

**Goal.** I3 response logic.

**Scope.**
- Recommended + good/better/best per item.
- A one-line trade-off for each: €/l or €/kg, pack, brand, stock certainty.
- Only proven-compliant products.

**Acceptance.** Alternatives are shown for A and B. None of them violates a hard requirement.

### T23: Scenario B2: partial-delivery proposal versions
Lane: core · Priority: P1 · Depends on: T06, T10
Touches: backend/app/gateway/offers.py, backend/app/gateway/proposals.py, backend/tests/test_partial.py

**Goal.** P8 B2.

When the need cannot be fully met, the gateway produces an explicit partial-delivery proposal version (e.g. "95 kg of 120 kg on Mon 16 Nov"). That version gets its own decision and its own approval.

**Acceptance.** Eval row 21 on fixtures:
- 474235 × 19 + 468465 × 60 = €505.68;
- ASK with `[DATE_LATER, PARTIAL_AVAILABILITY, OVER_AUTO_LIMIT]`;
- 473895 and 471108 excluded with CUTOFF_PASSED;
- the approver sees "Approving means 95 kg instead of 120 kg, on Monday instead of today."

### T24: Editable assumption chips
Lane: web · Priority: P1 · Depends on: T10, T11
Touches: frontend/src/components/request/**

**Goal.** §7.2: the agent's assumptions are visible, and editing one re-runs the flow.

**Acceptance.** Changing "any fat %" to "1.5 %" in A re-runs the flow and changes the shortlist.

### T25: Finnish input polish
Lane: agent · Priority: P1 · Depends on: T09
Touches: backend/app/catalog/vocab.py, backend/app/agent/parse.py, backend/tests/test_parse_fi.py

**Goal.** Finnish kitchens type in Finnish.

**Scope.**
- Wider vocabulary: inflections, compounds, units ("litraa", "kiloa", "laatikkoa").
- Weekday forms ("maanantaiksi", "huomiseksi").
- Finnish versions of the eval rows.

**Acceptance.** The Finnish A, B, C and B2 produce the same decisions as the English versions.

### T26: MCP agent interface
Lane: api · Priority: P2 · Depends on: T08
Touches: backend/app/api/mcp.py, docs/API.md, backend/tests/test_mcp.py

**Goal.** Any MCP agent can run the flow.

**Scope.**
- Streamable HTTP at `/mcp`.
- The agent-channel tools only. **No decideApproval.**

**Acceptance.** Claude Code connected through `claude mcp add` reaches PENDING_APPROVAL for B.

### T27: Valio Aimo readiness view
Lane: web · Priority: P2 · Depends on: T02, T11
Touches: backend/app/gateway/insights.py, frontend/src/pages/ValioView.tsx

**Goal.** §14, D6: data quality = autonomous sales.

**Scope.**
- Data gaps by supplier and which of them block AUTO.
- Live AUTO/ASK/BLOCK statistics.
- A fix list.

**Acceptance.** Every number is computed from data.

### T28: Hash-chain tamper evidence
Lane: core · Priority: P2 · Depends on: T07
Touches: backend/app/gateway/log.py, frontend/src/pages/Log.tsx

**Goal.** A sha256 hash chain over the decision log, a verify endpoint, and a "verified ✓" badge.

**Acceptance.** A test detects a tampered entry.

### T29: Open-spec page (Agent Trust Labels v0.1)
Lane: web · Priority: P2 · Depends on: T08
Touches: frontend/src/pages/Spec.tsx, spec/**

**Goal.** S7: a human-readable spec page.

**Scope.** Trust levels, facts, reason codes with their approvability, the operations, and links to OpenAPI and the JSON Schemas.

**Acceptance.** Linked from the main view.

### T30: Audit export
Lane: api · Priority: P2 · Depends on: T07
Touches: backend/app/api/log_routes.py

**Goal.** Export the decision log per proposal or customer as JSON and CSV.

**Acceptance.** The export of Scenario B contains every transition.
