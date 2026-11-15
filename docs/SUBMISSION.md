# Aimo Connect

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
  - "1,000 litres of lactose-free milk by Monday" is confirmed automatically at €1,010.00, inside the hotel's €2,500.00 auto-approve limit.
  - A €506.16 gluten-free school order above its €500.00 auto-approve limit waits for the Head Chef.
  - €20,200.00 above a €15,000.00 hard limit is blocked, and the attempted "ignore the hard limit" instruction is logged as ignored. A request that tries to skip approval is never ordered automatically: a person checks it. The block also shows what would fit within the limit (14,851 l for €14,999.51), as information only.
  - The figures come from the live API at request time.
- **Evidence with provenance:**
  - Every decisive fact shows Verified, Declared, Estimate or Unknown, together with its source and retrieval time.
  - Lactose-free does not imply milk-free.
  - A supplier-listed substitute must prove the buyer's requirements too. The school order rejects a listed oat drink that lacks the gluten-free claim.
  - A different product type, such as cooking cream when whipping cream was asked for, is never auto-approved.
  - A stated product form (grated, sliced, block) is a requirement: another form is at most a substitute for a human to approve.
  - Quantities are sized in whole sales units.
- **Humans approve trade-offs, never missing evidence:**
  - The Approver panel and a phone page show the exact lines, quantities, delivery, total and reasons, in plain words. Example: "Approving means 95 kg instead of 120 kg, on Monday instead of today."
  - The single-use demo permission is bound to that proposal version. The agent's responses never contain an approval link, token or QR.
  - Before confirmation, the server re-checks price, quantity, delivery and requirements. Any change voids the old approval and decides a new version from scratch.
  - Unknown allergens and passed cut-offs cannot be waived.
  - When nothing qualifies, the result says what was searched, offers the earliest deliverable date as a one-click retry, and lists the closest compliant products (nothing is ordered from them).
  - Partial stock becomes its own proposal version with its own approval.
  - When one line of a multi-item request cannot be met, the deliverable lines become their own proposal for a person, who sees which line is not ordered and why. The missing line is never ordered.
  - When another compliant product has the missing stock, it tops up the rest as a split order. A top-up of the same product type can complete automatically within the limits; any other top-up goes to a person, and a price rise of more than 10 % per kg or litre is stated.
- **Alternatives and editable assumptions:**
  - Each line shows Good / Better / Best alternatives with a one-line trade-off (price per litre or kilo, pack, brand, stock certainty, labels and CO₂ estimate). Only compliant products are listed.
  - The agent's assumptions are editable chips, for example "Any fat %" → "1.5 %". Editing one re-runs the flow, and the gateway's shortlist changes.
- **Buyer control:** the mandate reads as three plain lists: what the agent may order alone, what needs a person, and what is always refused. A buyer admin (`/admin`) can change the limits, allowed product categories and the validity window. Open proposals are then re-decided live, and the change is logged. The mandate can be reset to its defaults.
  - The same buyer's orders for the same delivery day count together against the limits, so a need split into small orders cannot slip under the auto-approve limit. Daily limits: hotel chain €2,500.00 auto-approve / €15,000.00 hard; school kitchen €500.00 / €6,000.00.
  - Each browser gets its own private session, so one visitor's changes never affect another's.
- **Inspectable and tamper-evident decisions:**
  - The decision log shows actors, rules and evidence for every state change. `/log` lists recent proposals per buyer.
  - A SHA-256 hash chain over the log can be verified at `/v0.1/audit/verify`; a buyer's agent key verifies that buyer's own chain.
  - Offers, decisions and confirmations carry Ed25519 signatures. The public key is published at `/.well-known/aimo-connect-keys.json`, and an exported log can be verified offline (signatures and hash chain).
  - The log downloads as JSON or CSV from the log page, and it shows the result of the final re-check.
- **Open interface:**
  - OpenAPI 3.1, JSON Schemas and a human-readable specification of the trust labels and reason codes at `/spec`, including how the labels map to GS1 product data and W3C Verifiable Credentials concepts.
  - Agents can ask for a compact response (`?view=compact`): a one-line summary, every reason with its plain-language text, and, when nothing can be ordered, the closest matches, the earliest deliverable date and a ready retry request.
  - An MCP endpoint at `/mcp` exposes the purchasing-agent tools, buyer-scoped resources (playbook, mandate, trust labels) and an order-request prompt. Approval is not an agent tool and is refused if called.
  - A proposal must describe its offer truthfully: request items that contradict the bound offer lines are rejected.
  - Agents identify their buyer with a public key, `Authorization: Bearer demo-agent-<customerId>`, and can act only for that buyer. Approvers sign in by role and see only their buyer's queue, and can enrol a passkey on their phone so that approving requires it; buyer-admin changes need the public admin key shown on `/admin`.
  - A second, independent client gets the same decision and offer hash.
- **Valio Aimo readiness view** (`/valio`): computed from the 17,546-product catalogue.
  - An agent activity feed lists every agent call, refused ones included. Valio can pause all agents, and an agent with repeated refused calls is suspended; a rogue-agent run shows the refusals live.
  - It shows which data gaps keep products from automatic ordering, by supplier.
  - It shows live AUTO / ASK / BLOCK statistics and a prioritised fix list.

## Try it

1. Open the live demo and run "I need 1,000 litres of lactose-free milk by Monday."
2. Pick Scenario B and open the Approver panel in a second window, or on a phone via its QR code. Approve.
3. Pick Scenario C.
4. On the official example, change the "Any fat %" assumption chip to "1.5 %". The flow re-runs and the shortlist changes.
5. With Scenario B pending, change the school's auto-approve limit in `/admin`. The open proposal is re-decided, and the change appears in its decision log. "Reset" restores the defaults.
6. Open the decision log of any proposal: actors, rules, evidence, the final re-check, and JSON/CSV download. Then open `/spec` and `/valio`.
7. Exceptions: ask for "200 kg of grated emmental by Monday" (no single product has enough stock, so the order is covered by two compliant products, rounded up to whole packs), and "40 kg of butter for today" (the cut-off has passed; the result offers the earliest deliverable date as a one-click retry).
8. On `/valio`, start the rogue-agent run and watch its refused calls lead to a suspension; then pause all agents.

## Limits and production path

No real order or payment is sent: a confirmed order ends in a simulated confirmation, because the challenge has no order API.

The challenge's availability API currently offers every product for delivery on Mon 16 Nov, whatever date is requested. A delivery earlier than requested is therefore shown as information, and every order delivers on that date.

The keys and role sign-in are public demonstration gates that show the separation of duties; they are not production authentication. State is held in memory and resets when the service restarts.

Production would need:
- authenticated named approvers (SSO/OIDC);
- scoped agent credentials;
- durable storage;
- a real supplier order integration.
