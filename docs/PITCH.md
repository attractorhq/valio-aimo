# Aimo Connect — three-minute pitch

**Status:** the flow is verified locally; a public deployment has not yet been verified. Present from the local stack until the architect supplies a verified URL. The amounts below are reference expectations, not outputs to hardcode or assert when current data differs.

Run `scripts/preflight` immediately before presenting. GREEN: use the live mock API. RED: show the differences, launch an actual private recorded capture, and show its capture time. Say: “This is a recorded replay, not a live quote.” Never use the synthetic UI fallback as live evidence. [The rehearsal guide](DEMO.md) contains the detailed window layout and commands.

## Spoken script and screen cues

### 0:00–0:20 — the kitchen's problem

Show the buyer's request and mandate. Say:

> A kitchen needs the right food, on time, within its authority—not another chatbot.
>
> Same interface, three outcomes: it orders on its own, asks a human at exactly the right point, and refuses to break a hard limit.

### 0:20–0:50 — A: AUTO

Run Scenario A. Show Request → Lookup → Order → Approval, the trust cards, €1,010.00 excl. VAT and simulated confirmation. Say:

> The hotel needs 1,000 litres of lactose-free milk by Monday. We shortlist from the catalogue, then read price and availability only for those candidates. The claim is declared, the live stock and date are verified, and the total is inside the hotel's mandate. The server re-checks before confirming. No human click—and no real order is sent in this mock environment.

When replaying a recording, replace “live stock” with “stock in this capture”; do not claim fresh verification.

### 0:50–1:55 — B: ASK, then the human approves version 1

Run Scenario B. Show the single `OVER_AUTO_LIMIT` reason, rejected listed substitute, and lack of approval controls in the buyer view. Say the following only when the displayed terms match:

> Everything is proven — gluten-free declared, stock verified, Monday confirmed. It's €6.16 over the school's €500 limit, so one tap from the head chef. And look: Valio's own listed substitute would have broken the gluten-free rule, so the agent will never swap to it.
>
> A human approves trade-offs, never missing evidence. Unknown allergens and passed cut-offs are never up for approval.

Switch to `/approver`. Point out the exact version, quantity, date, total and “Demo approval link — single use”. Approve €506.16 and show the final state in both views. Say:

> The approval belongs to these exact terms. Before confirming, the server re-checks. If a decisive term changes, this approval is void and a new proposal is decided from scratch. The language model never decides AUTO, ASK or BLOCK.

“Monday confirmed” refers to the checked mock delivery date, not a real supplier booking. If pre-flight or the screen differs, explain the actual result rather than recite an outdated amount.

### 1:55–2:25 — C: BLOCK

Run Scenario C. Show €20,200.00 above the €15,000.00 hard limit (with 14,851 l shown as feasible within the limit, information only) and `INSTRUCTION_IGNORED`. Say:

> The request claims the CFO already approved a bigger budget. Request text is data, not authority. The gateway logs the instruction attempt and blocks the hard-limit breach. A human cannot approve away a hard limit or missing allergen evidence.

### 2:25–2:50 — an open boundary, not a black box

Show `/openapi.json`, run `scripts/second-client http://localhost:8000` (or the verified demo URL), then show a decision log. Say:

> Here's the OpenAPI spec. Here's a second client getting the exact same decision.
>
> The log shows who acted, which rule applied and which evidence mattered. Production needs authenticated approvers and durable records; this demo uses single-use links and resets on restart. MCP is a next interface, not something we're claiming today.

The comparison is the policy decision for the same terms, not a promise that independent live fetches have identical timestamps or hashes. The client stops at pending approval; it cannot sign for the human.

### 2:50–3:00 — close

> Same interface, three outcomes: it orders on its own, asks a human at exactly the right point, and refuses to break a hard limit.
>
> Aimo Connect. Simulated orders today; an explicit trust and authority boundary for production.

## Coverage of the five official judging criteria

| Criterion | Weight | Evidence to show, not just claim |
|---|---:|---|
| Working end-to-end demo | 30% | A → B → C in one interface; B approved in a separate human view; server re-check and final simulated confirmation. |
| Solving the user's need | 20% | Kitchen quantities and date, physical-to-sales-unit sizing, dietary requirements, a clear total, and an exact reason when human attention is needed. |
| Autonomy and controls | 20% | Buyer mandate, deterministic AUTO/ASK/BLOCK, exact-version single-use approval, no agent approval capability, and C's hard-limit/instruction boundary. |
| Robustness to exceptions | 15% | Rejected supplier-listed substitute, excluded missing evidence, price-change supersession, live pre-flight and honest recorded fallback; request interpretation has a deterministic fallback. |
| Openness and path to production | 15% | OpenAPI 3.1, JSON Schemas, independent REST client and decision log; explicit SSO/OIDC, scoped credentials, durable storage, real-order integration and MCP roadmap. |

## Evidence discipline for Q&A

- **“Did all 24 evaluation cases pass?”** No. Current developer result: **8/24**, eight active passed, sixteen pending, using reference fixtures. Pending is not passed; this does not test the whole free-text planner. Obtain a separate report on private recorded captures before quoting a recorded-data score.
- **“Is this real approval security?”** No. Demo links are single-use and version-bound, but the approver/admin channels have no production authentication. SSO/OIDC and customer-scoped authorisation are required.
- **“Is the audit tamper-proof?”** No. It is a readable decision log, currently volatile. Durable storage and hash-chain evidence are future work.
- **“Can another agent connect?”** Via documented REST today. MCP is roadmap. No client gets an approval or confirmation tool.
- **“Does declared mean certified safe?”** No. The label preserves supplier evidence and its strength; the challenge data is not production food-safety certification.
- **“Is this deployed?”** Not yet verified in this revision. Do not claim a public deployment until its URL and the full flow have been checked.
