# Trusted Trade Agents — by Valio Aimo (Junction 2026)

*Official challenge text as published on the Junction platform. Access credentials removed.*

## The Challenge

How can a food-service wholesaler let autonomous purchasing agents place an order – finding products, comparing offers and getting the right approval in a fraction of the time it takes today – without exposing real systems, confidential data or the marketplace roadmap?

## Introduction

Valio Aimo® serves 20,000+ professional kitchens and is planning to build a digital B2B marketplace connecting foodservice buyers and suppliers. As ordering shifts from people clicking through a catalog to AI agents making autonomous purchasing decisions, the question of how a wholesaler exposes itself to those agents – what they can see, decide and be trusted with – becomes existential for data ownership, trust, and who owns the customer relationship.

The Digital B2B Marketplace is a future step in this direction; for Junction 2026 we are mocking today's webshop and backend systems, not the marketplace itself. We want you to prototype the interface and operating model that would let an external purchasing agent interact with Valio Aimo's systems, built on open, verifiable standards rather than a single platform's black box.

## The challenge

B2B ordering in foodservice and wholesale is still largely manual or menu-driven: a buyer logs into today's Valio Aimo webshop and searches a catalog. The step we want to explore is external purchasing agents that find, compare, order and handle exceptions on the buyer's behalf, working with Valio Aimo directly – not by building a whole marketplace, but by defining the interface and operating model through which an external agent could interact with Valio Aimo's systems at all.

### Core – your solution must enable

- **Product, price & availability lookup:** a structured way for an external agent to search for and retrieve what's available and at what price.
- **Requesting price & availability, then placing an order:** a defined interaction for moving from inquiry to commitment, based on fixed prices rather than a negotiated quote.
- **Permission & human-approval management:** what the agent may decide on its own, and what must go to a human for sign-off.

### Optional – open directions (not required to win)

- **Delivery time & sustainability comparison:** not just price, but delivery reliability and the certificates/sustainability labels already shown in the webshop. (Category-level carbon-footprint ranges are a stretch goal.)
- **Substitution and other exception handling:** what happens when the exact request can't be met. (Last year's Zero-Fail Logistics challenge covered failed deliveries in depth, so this isn't scored as core.)
- **Traceability and explainability:** a human-readable record of what the agent did and why.

### Example request

"I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on price and availability."

A hotel chain's agent sends this request. Your solution returns products, prices and availability in structured form; the agent places the order within its authority or sends it to a human for approval.

**Impact:** This is not about replacing today's e-commerce UI with a chatbot. It is about defining how a European food-service wholesaler can be safely and openly readable by autonomous agents – before that interface gets defined by someone else's platform.

## Valio Aimo®

Valio Aimo® was created in 2021 by Valio, transforming traditional wholesale into a foodservice company. 20,000 professional customers across restaurants, foodservice operators and industrial customers. 25,000 product names sourced from 700 suppliers. 350 specialists. Part of Valio, Finland's leading dairy and food company.

## Insight — problems we'd love to see tackled

- **Trust protocol:** the minimum data/verification standard for an external agent to trust Valio Aimo's claims on stock, price and origin without a manual check.
- **Guardrails model:** which decisions require human sign-off (spend threshold, substitutions outside stated preferences) vs. fully autonomous.
- **Response logic:** how an agent handles partial availability and substitution ranking ("good-better-best") within Valio Aimo's own assortment.
- **Audit trail:** a usable, human-readable log of an agent's purchasing decisions, for both buyer and Valio Aimo.

## Example scenarios to spark ideas

- **Need → recipe → order:** a chef plans next week's menu around a seasonal ingredient. The agent suggests recipes, works out what's missing from stock, and places the order.
- **Last-minute event:** an unexpected booking for tomorrow. The agent identifies what the recipes require, flags what can't be sourced in time, and proposes substitutions through recipe logic rather than a simple SKU swap.
- **Supplier stock-out:** a key product runs out mid-week. The agent proposes a recipe-compatible substitute before the kitchen notices the gap, and explains the trade-off.
- **Proactive selling agent:** Valio Aimo's own agent spots a new or seasonal product and proactively suggests it to the buyer's agent.
- **Allergen-safe substitution under pressure:** in a school or hospital kitchen, a substitution must still satisfy strict allergen and nutrition rules.
- **Standing order drift detection:** the agent learns a kitchen's recurring weekly order and flags anomalies (portion counts creeping up, cost drifting) before auto-renewing it.

**Scope note:** we are mocking a structured data/API interface. Whether Valio Aimo's production interface eventually becomes a traditional API, an MCP-style agentic interface or a full service agent of its own is outside the scope of this challenge.

## Resources (given to all teams)

Synthetic product catalog (about 17,500 products), simulated suppliers and mock APIs mimicking Valio Aimo's webshop/backend systems: prices, availability behaviour, delivery lead times, delivery reliability, sustainability labels and category-level carbon footprint. No real-time, live or confidential data. All provided data is synthetic.

Preferred tech: AI agents (agentic/tool-use frameworks), open data/interop standards, recommendation logic, optional verifiable-credential or trust-layer approaches. Agentic payment standards (e.g. x402, Nevermined) are relevant context but not required.

## Judging criteria

| Criterion | Weight | Description |
|---|---|---|
| Working end-to-end demo | 30% | The solution runs through a full request → lookup → order/approval flow in the mocked environment, not just isolated pieces. |
| Solving the problem and user need | 20% | How precisely the agent matches what the buyer actually asked for, with the least manual correction needed. |
| Autonomy and controls | 20% | Quality of the guardrails design. What the agent can decide alone, what needs human sign-off, and how decisions are logged and auditable. |
| Robustness to exception situations | 15% | Handling of partial availability, substitutions and delivery constraints beyond the happy path. |
| Openness and credible path toward production | 15% | Open, documented standards rather than a proprietary format, clear visibility of assumptions, well-justified technical choices, and a credible view of next development steps. |

Prizes: 1st €4,000 · 2nd €2,000 · 3rd €1,000.

## Printed challenge flyer (Valio Aimo, Junction 2026)

Build the prototype that lets an AI purchasing agent buy food from a wholesaler: safely, openly, and with the right human approval.

**The idea.** Ordering is moving from people clicking through a webshop to AI agents buying on their behalf. Valio Aimo serves 20,000+ professional kitchens with 25,000 products from 700 suppliers. We want to define how an outside agent can work with our systems on open standards, not inside someone else's black box.

**The flow your solution must run:**
1. Request: the agent asks for what the buyer needs.
2. Lookup: find products, prices and availability.
3. Order: commit at a fixed price.
4. Approval: the agent decides, or a human signs off.

Example: a hotel chain's agent asks, "I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on price and availability."

**Must build (core):**
1. Lookup: structured search of products, prices and availability.
2. Request and order: from inquiry to commitment at fixed prices.
3. Permissions and approval: what the agent decides alone, and what a human signs off.

**Bonus ideas (optional):**
- Compare more than price: delivery reliability and sustainability labels.
- Handle exceptions: partial availability and substitutions when the exact request cannot be met.
- Explain decisions: a human-readable record of what the agent did and why.

**How we judge:**

| Criterion | Weight |
|---|---|
| Working end-to-end demo | 30% |
| Solving the user's need | 20% |
| Autonomy and controls (guardrails) | 20% |
| Robustness to exceptions | 15% |
| Openness and path to production | 15% |

Trust and openness by design: a full flow that works, a clear model for human sign-off, and open, well-documented choices. **Not** a production-ready system in 48 hours.

**You get:** pricing and availability mock APIs; product data, delivery reliability and carbon footprint files; mentors for AI, wholesale and data governance.

**Good to know:**
- We mock today's webshop, not the marketplace.
- It is not a chatbot.
- Query the APIs only when needed, never for all products.

## Official Q&A from Valio Aimo (challenge channel, 14 Nov)

- **Orders:** there is no order API in this challenge. It is enough that a solution produces orders for human approval; they don't need to be sent anywhere. A webshop account is available at the booth, but orders cannot be placed in it (it is production).
- **closingTime:** the order cut-off for the related availableDate. The mock data stays the same for the weekend; in real life it would shift.
- **Availability ≥ 999999:** Valio does not know the availability. Its current logic allows ordering any quantity. These values typically appear on terminal products that Valio does not stock. An empty availability means not available.
- **allowedLotSize = 0.01:** treat it as 1. Fractional crates are not possible.
- **Rate limits:** hitting the limit returns HTTP 429 and blocks the caller for 30 seconds, so solutions should honour 429 responses. A few hundred calls per minute is fine; that many within 10 seconds is not.
- **Approvals today:** Valio's systems have no approval gates for orders. Customers with spending or decision limits handle them through internal instructions, or by letting only the manager place orders. In an agentic scenario a human approval would be required at this point, though it might be dropped in the future if reliability is good enough.
