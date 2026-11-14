# Verified or Ask

**Same interface, three outcomes: it orders on its own, asks a human at exactly the right point, and refuses to break a hard limit.**

Team Attractor · Valio Aimo challenge "Trusted Trade Agents" · Junction 2026

**Live demo:** https://valio-aimo.attractorfi.workers.dev. One origin for the purchasing UI, the Approver panel (`/approver`), the REST API (`/v0.1`, OpenAPI at `/openapi.json`, docs at `/docs`) and an MCP endpoint (`/mcp`). It runs against the challenge's live mock price and availability API.

## What it is

A trusted trade gateway for Valio Aimo plus a demo purchasing agent. It separates understanding a kitchen's request from having the authority to act. The request is interpreted into a structured order. Deterministic gateway rules then decide AUTO, ASK or BLOCK against a buyer-owned mandate, and every outcome carries a reason code.

## What the live demo shows

- **Full purchasing flow:** Request → Lookup → Order proposal at a fixed price → Approval → simulated confirmation.
  - Static catalogue search narrows the candidates first. Price and availability are then read live for a short list only.
  - The interface is a structured control room, not a chat thread.
  - Free text works in English and Finnish ("Tarvitsen tuhat litraa laktoositonta maitoa maanantaiksi").
- **One interface, three outcomes:**
  - "1,000 litres of lactose-free milk by Monday" is confirmed automatically at €1,010.00, inside the hotel's mandate.
  - A €506.16 gluten-free school order above its €500.00 auto-approve limit waits for the Head Chef.
  - €20,200.00 above a €5,000.00 hard limit is blocked, and the attempted "ignore the hard limit" instruction is logged as ignored. The block also shows what would fit within the limit, as information only.
  - The figures come from the live API at request time.
- **Evidence with provenance:**
  - Every decisive fact shows Verified, Declared, Estimate or Unknown, together with its source and retrieval time.
  - Lactose-free does not imply milk-free.
  - A supplier-listed substitute must prove the buyer's requirements too. The school order rejects a listed oat drink that lacks the gluten-free claim.
  - A different product type, such as cooking cream when whipping cream was asked for, is never auto-approved.
  - Quantities are sized in whole sales units.
- **Humans approve trade-offs, never missing evidence:**
  - The Approver panel and a phone page show the exact lines, quantities, delivery, total and reasons, in plain words. Example: "Approving means 95 kg instead of 120 kg, on Monday instead of today."
  - The single-use demo permission is bound to that proposal version. The agent's responses never contain an approval link, token or QR.
  - Before confirmation, the server re-checks price, quantity, delivery and requirements. Any change voids the old approval and decides a new version from scratch.
  - Unknown allergens and passed cut-offs cannot be waived.
  - Partial stock becomes its own proposal version with its own approval.
- **Alternatives and editable assumptions:**
  - Each line shows Good / Better / Best alternatives with a one-line trade-off (price per litre or kilo, pack, brand, stock certainty). Only compliant products are listed.
  - The agent's assumptions are editable chips, for example "Any fat %" → "1.5 %". Editing one re-runs the flow, and the gateway's shortlist changes.
- **Buyer control:** a buyer admin (`/admin`) can change the mandate's limits. Open proposals are then re-decided live, and the change is logged. The mandate can be reset to its defaults.
- **Inspectable and tamper-evident decisions:**
  - The decision log shows actors, rules and evidence for every state change.
  - A SHA-256 hash chain over the log can be verified at `/v0.1/audit/verify`.
  - The log downloads as JSON or CSV from the log page, and it shows the result of the final re-check.
- **Open interface:**
  - OpenAPI 3.1, JSON Schemas and a human-readable specification of the trust labels and reason codes at `/spec`.
  - An MCP endpoint at `/mcp` exposes the purchasing-agent tools only. Approval is not an agent tool and is refused if called.
  - A second, independent client gets the same decision and offer hash.
- **Valio Aimo readiness view** (`/valio`): computed from the 17,546-product catalogue.
  - It shows which data gaps keep products from automatic ordering, by supplier.
  - It shows live AUTO / ASK / BLOCK statistics and a prioritised fix list.

## Try it

1. Open the live demo and run "I need 1,000 litres of lactose-free milk by Monday."
2. Pick Scenario B and open the Approver panel in a second window, or on a phone via its QR code. Approve.
3. Pick Scenario C.
4. Open the decision log of any proposal, `/spec` and `/valio`.

## Limits and production path

**Simulated order — mock environment.** No real order or payment is sent.

Demo approval links are not an authenticated identity, and the demo has no production access control. State is held in memory and resets when the service restarts.

Production would need:
- authenticated named approvers (SSO/OIDC);
- scoped agent credentials;
- durable storage;
- a real supplier order integration.
