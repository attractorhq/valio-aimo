# Synthetic v0.1 UI mocks

These hand-made objects are **not API recordings or catalogue records**. All product IDs and names are synthetic. They follow the Scenario A/B shapes and illustrative quantities/totals, not live facts. The fixed hashes are placeholders, not computed commitments. Do not use these objects as policy results or live demo data.

- `scenario-a.json`: a confirmed simulated AUTO proposal, 1,000 sales units, €1,010.00 excl. VAT.
- `scenario-b.json`: a pending ASK proposal, 18 five-kg bags + 80 one-litre pieces, €506.16 excl. VAT, exactly `OVER_AUTO_LIMIT`. Excluded candidates do not become proposal-level approval reasons.
- `mandate-m1.json`, `mandate-m2.json`: corresponding synthetic read-only mandate examples.

The backend contract tests validate every JSON file against its Pydantic model and checked-in JSON Schema. The `RECORDED` flag exercises the recorded-mode shape; its timestamp is synthetic too. A UI using these files must prominently say **Synthetic mock data**, not imply that they were captured from Valio.
