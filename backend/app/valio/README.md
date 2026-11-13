# Valio v0.1 reads and fixture replay (T03)

This package reuses the original adapter's typed upstream models, Decimal parsing, authenticated requests and batch-binding checks. It adds per-product evidence, explicit live/recorded sources and a private recorder. The legacy `RealValioProvider` still serves the old `/api/v1` application until T08; it now imports its vendor models from here. No policy decisions or order confirmations happen in this package.

## Integration

```python
from app.valio import LiveValioProvider, RecordedValioProvider, ValioClient

# Reuse the provider/client to benefit from the bounded 45-second TTL cache.
provider = LiveValioProvider(ValioClient())
snapshot = provider.lookup(
    {"request-item-1": shortlisted_ids}, requested_date, customer_id,
    products=normalised_products_by_id,
)

# Confirmation re-checks MUST bypass both price and availability caches.
fresh = provider.lookup(
    {"request-item-1": shortlisted_ids}, requested_date, customer_id,
    products=normalised_products_by_id, force_refresh=True,
)

# Explicit offline selection: never a fallback when a live call fails.
recorded = RecordedValioProvider("data/fixtures/<capture-ts>")
snapshot = recorded.lookup(
    {"request-item-1": shortlisted_ids}, requested_date, customer_id,
    products=normalised_products_by_id,
)
```

`ValioSnapshot` contains `products[productId]`, logical `customer_id`, requested date, `now`, capture time, `mode` (`LIVE | RECORDED`, matching T01), and `recorded_at`. Map the last field to API/Offer `recordedAt`; visibly label recorded mode and its capture time. No separate API route is introduced by T03; T06/T08 project this evidence into the shared wire contracts.

`ProductFacts` combines `PriceFacts`, `AvailabilityFacts`, trust labels and candidate reason codes. These are **observations, not mandate decisions**. The policy layer decides whether an open-cutoff Terminal product needs approval under a particular mandate. Supply normalised `Product`s to check/convert availability sales units against catalogue units; without catalogue input, `unit.sales` describes the upstream unit only and does not prove compatibility with a proposed quantity.

- Prices, API VAT, validFrom/validTo, comparison price/unit, list price and customer-price indication are retained. No VAT is inferred from product data. Missing VAT makes the product unusable (`PRICE_UNAVAILABLE`), not zero-tax. Optional comparison/list prices must also be valid EUR prices.
- Stock tiers: `EXACT` stores the Decimal quantity; `PLENTY` retains the API's verified large amount for bounded sufficiency checks but displays "Plenty in stock", never an assertion of infinite inventory; `SUPPLIER` (Terminal) has no quantity, never the sentinel 999999; `UNAVAILABLE` is a negative answer; malformed/unknown observations are `UNKNOWN` and unusable.
- A later date remains VERIFIED with `DATE_LATER`. Cut-off compares against the API's own `timeNow` (not the machine clock), marks K3, and emits `CUTOFF_PASSED`. A missing cut-off is UNKNOWN and cannot silently promise delivery (`NOT_AVAILABLE`).
- Exact `units[]` conversions resolve a sales-unit mismatch. Unresolvable or nonterminating Decimal conversions are K4/`UNIT_MISMATCH`; no quantity is silently rounded into evidence. This conservative behaviour may leave sub-sales-unit fractions unusable.
- Per-row invalid prices/schedules exclude that candidate, leaving valid neighbours usable. Batch-level wrong customer/currency/date or missing/duplicate/unexpected product IDs abort the entire read: cross-customer or incomplete envelopes cannot be trusted.
- `now` is always the upstream availability `timeNow`, frozen verbatim in recorded mode. `price_retrieved_at` is the actual read time and remains frozen in a recording. Replaying ignores `force_refresh` for network purposes but re-reads private fixture files (useful for re-check change tests).

`ValioClient` requires 1–20 shortlisted IDs **per request item**, deduplicates across items, and caps a batch at 1,000 unique IDs. It makes one price and one availability POST per evaluation (except bounded retries). It maps logical customers with existing `VALIO_CUSTOMER_IDS`, falling back to the configured upstream `VALIO_CUSTOMER_ID` as the original adapter did. Cache keys include upstream customer, date, endpoint and canonical product set. Only bound, valid-JSON success envelopes are cached; mutable callers do not share cached objects.

The default TTL is 45 s (configurable 30–60), with at most 128 cached responses. HTTP 429/5xx retries twice with 0.25/0.5 s backoff; numeric Retry-After is honoured up to 5 s. Authentication/redirect errors do not retry. Network errors do not silently switch to fixtures. Timeouts use `VALIO_TIMEOUT_SECONDS`; settings and API keys are server-only. Raw bodies, headers and credentials are never included in integration errors. All dynamic redirects are refused.

## Private fixture recorder

From the worktree root, with your event key configured server-side:

```bash
scripts/record-fixtures --date 2026-11-16 --date 2026-11-13 \
  --customer nordic-harbour-hotels --customer northbay-school-kitchen
```

The defaults include all 14 documented demo product IDs and discover additional six-digit references in the sanitised brief/patch. To cover additional actual scenario shortlists, use repeatable `--product-id` or `--shortlists-file <private-json>` (a map from item IDs to at most 20 IDs per item). If the brief references exceed 20, supply an explicit per-item plan rather than expanding a single shortlist. Requests are always reads; no order endpoint is called.

The recorder writes `data/fixtures/<UTC-capture-ts>/manifest.json`, plus unmodified `price-NNN.json` and `availability-NNN.json` responses. Each manifest batch binds the logical/upstream customer, requested date, product IDs and retrieval time. Server `timeNow` stays in the raw availability response. Captures use private file/directory permissions and are only written after every read succeeds. Invalid/incomplete captures fail closed; paths escaping the capture directory are refused. Keep the entire directory private and gitignored—never commit recordings or publish them in a PR. The UI mock JSON files are synthetic, not compatible recordings.

## Observed upstream shape

A single documented milk shortlist product was read on 13 Nov 2026 to verify response **field names/types only**. The price response includes `catalogPrice`, `comparisonPrice`, `comparisonUnit`, `listPrice`, `netPriceFound`, and `tax.{taxRate,taxValue}`; availability includes `timeNow`, `requestedDate`, product/line sales blocks, logistics model, closing time and schedule lines. No raw response or product record was copied into tests. The original observed requested-date and price-validity quirks remain guarded explicitly.

All committed tests use hand-made records in `backend/tests/synthetic/valio.py` and `httpx.MockTransport`. They need neither the network, the dataset nor an API key. Replay and recorder tests use temporary synthetic captures, never the actual private recordings.
