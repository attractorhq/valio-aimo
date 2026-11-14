# Verified or Ask

**Same interface, three outcomes: it orders on its own, asks a human at exactly the right point, and refuses to break a hard limit.**

Team Attractor · Valio Aimo challenge "Trusted Trade Agents" · Junction 2026

## What it is

A trusted trade gateway for Valio Aimo plus a demo purchasing agent. It separates understanding a kitchen's request from having authority to act: the language model interprets the request, while deterministic gateway rules decide AUTO, ASK or BLOCK against a buyer-owned mandate.

## Availability and evidence

**Local prototype verified; public deployment not yet verified. No live demo URL is available in this submission.** The demonstrated capabilities below have local verification against the challenge's live mock API and private recorded captures; they are not claims of a deployed service.

## Demonstrated locally

- **Full purchasing flow:** Request → Lookup → Order proposal at fixed price → Approval → simulated confirmation. Static catalogue search narrows candidates before batched price and availability reads; the interface is a structured control room, not a chat thread.
- **One interface, three outcomes:** the reference walkthrough automatically confirms €1,010.00 of lactose-free milk within the hotel's mandate; asks the school's Head Chef about a €506.16 gluten-free order above its €500.00 auto-limit; and blocks €20,200.00 above a €5,000.00 hard limit, logging an attempted instruction override. Live prices, dates and cut-offs are checked, not assumed from these examples.
- **Evidence with provenance:** decisive facts show Verified, Declared, Estimate or Unknown, with their source. Lactose-free does not imply milk-free. Supplier-listed substitutes must prove the buyer's requirements too; the walkthrough rejects a listed oat-drink substitute that lacks the required gluten-free claim.
- **Approval of trade-offs, never missing evidence:** the human sees exact lines, quantities, delivery terms, total and reasons in a separate Approver view. Single-use demo permission binds to that proposal version and expires with the offer. Agent responses contain no approval link, token or QR. Before confirmation the server re-checks authority, price, quantity, delivery and hard requirements; changed terms invalidate the old approval and trigger a new decision. Unknown allergens and passed cut-offs cannot be waived.
- **Inspectable decisions and an open interface:** the decision log shows actors, rules and evidence. OpenAPI 3.1, JSON Schemas and an independent Python client expose the same deterministic policy through REST without giving the purchasing client approval powers.

## Try it

Run locally using the [README](../README.md). Start at `/` with “I need 1,000 litres of lactose-free milk by Monday.” Continue with Scenario B and the separate `/approver` view, then Scenario C. Run live pre-flight first; if it is RED, show the differences and use a visibly labelled recorded capture rather than claim a live run.

## Limits and production path

**Simulated order — mock environment.** No real order or payment is sent. Demo approval links are not authenticated identity; the demo has no production access control, and state resets on restart or deployment. Production requires authenticated named approvers (SSO/OIDC), scoped agent credentials, durable storage and a real supplier order integration. MCP is roadmap, not a delivered interface.

The current developer evaluation reports **8/24** using reference fixtures: all eight active rows passed and sixteen remain pending. This is not a 24/24 result or a deployed evaluation. Recorded replay shows its capture time; it is never presented as a fresh live response.
