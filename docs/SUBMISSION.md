# Verified or Ask

**Same interface, three outcomes: it orders on its own, asks a human at exactly the right point, and refuses to break a hard limit.**

Team Attractor · Valio Aimo challenge "Trusted Trade Agents" · Junction 2026

## What it is

A Valio Aimo-side trusted trade gateway that any purchasing agent can use, plus a demo purchasing agent that runs the full flow Request → Lookup → Order → Approval against Valio Aimo's mock price and availability APIs.

- **Safely.** Every fact the agent relies on (price, stock, delivery date, allergens, claims, pack size) carries a trust level and its source. A buyer-owned mandate and deterministic rules at the gateway decide whether an order goes through automatically or needs a human. The language model never makes that decision.
- **Openly.** The interface is documented in open, machine-readable formats, and any agent can connect to it.
- **With the right human approval.** Nothing is confirmed before the server has checked the agent's authority and re-checked live price and availability. A human approves business trade-offs, such as an order above the auto-approve limit. A human never approves missing evidence: unknown allergens and passed cut-offs are never up for approval. Approval happens in a separate approver view, for the exact proposal version the approver saw. Every step is logged in a decision log that the buyer and Valio Aimo can both read.

## Try it

- Live app: _(URL)_
- Example: "I need 1,000 litres of lactose-free milk by Monday."

All orders are simulated: there is no order API in the mock environment.
