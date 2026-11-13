"""Record narrow raw API batches into ignored, private data/fixtures/<capture-ts>/."""

import argparse
import json
import re
from datetime import date
from pathlib import Path

from .client import ValioClient, product_ids, utc_now
from .errors import IntegrationError

ROOT = Path(__file__).resolve().parents[3]
# Documented demo IDs, not product records. Never add raw response data to source control.
BRIEF_PRODUCT_IDS = (
    "464306", "465039", "468465", "469040", "470699", "470863", "471108",
    "471881", "473895", "474136", "474235", "475795", "460302", "460825",
)


def brief_product_ids(root: Path = ROOT) -> list[str]:
    ids = set(BRIEF_PRODUCT_IDS)
    for filename in ("brief.txt", "patch-v2.txt"):
        path = root / "docs/brief" / filename
        if path.is_file():
            ids.update(re.findall(r"(?<!\d)\d{6}(?!\d)", path.read_text()))
    # A documented supplier-stock sentinel is not a catalogue ID.
    ids.discard("999999")
    return sorted(ids)


def record_fixtures(client: ValioClient, shortlists, customers, dates, output_dir: Path) -> Path:
    product_ids(shortlists)
    batches = [client.fetch(shortlists, day, customer, force_refresh=True) for customer in customers for day in dates]
    if not batches:
        raise IntegrationError("Recording requires at least one customer and date.")
    captured_at = utc_now()
    target = output_dir / captured_at.strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries = []
    for index, batch in enumerate(batches, 1):
        batch.envelopes()
        price_name, availability_name = f"price-{index:03}.json", f"availability-{index:03}.json"
        for filename, body in ((price_name, batch.price_body), (availability_name, batch.availability_body)):
            path = target / filename
            path.touch(mode=0o600, exist_ok=False)
            path.write_bytes(body)
        entries.append({
            "customerId": batch.customer_id, "upstreamCustomerId": batch.upstream_customer_id,
            "requestedDate": batch.requested_date.isoformat(), "productIds": list(batch.product_ids),
            "priceFile": price_name, "availabilityFile": availability_name,
            "priceRetrievedAt": batch.price_retrieved_at.isoformat(),
        })
    manifest = target / "manifest.json"
    manifest.touch(mode=0o600, exist_ok=False)
    manifest.write_text(json.dumps({"version": 1, "capturedAt": captured_at.isoformat(), "batches": entries}, indent=2))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customer", action="append", help="Logical customer ID; repeat for multiple mandates.")
    parser.add_argument("--date", type=date.fromisoformat, action="append", required=True, help="YYYY-MM-DD; repeatable.")
    parser.add_argument("--product-id", action="append", default=[], help="Additional shortlisted ID; repeatable.")
    parser.add_argument("--shortlists-file", type=Path, help="Private JSON mapping item IDs to lists of at most 20 IDs.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/fixtures")
    args = parser.parse_args()
    try:
        shortlists = {"brief-and-patch": brief_product_ids()}
        if args.shortlists_file:
            extra = json.loads(args.shortlists_file.read_text())
            if not isinstance(extra, dict) or "brief-and-patch" in extra:
                raise ValueError("Invalid shortlist mapping")
            if len(shortlists["brief-and-patch"]) > 20:
                if not set(shortlists["brief-and-patch"]).issubset(product_ids(extra)):
                    raise ValueError("The explicit recording plan must include every brief product ID")
                shortlists = extra
            else:
                shortlists.update(extra)
        if args.product_id:
            shortlists["extra-shortlist"] = args.product_id
        client = ValioClient()
        customers = args.customer or list(client.settings.valio_customer_ids) or [client.settings.valio_customer_id]
        target = record_fixtures(client, shortlists, customers, args.date, args.output_dir)
        print(f"Recorded private capture: {target} ({len(product_ids(shortlists))} products per batch)")
    except (IntegrationError, ValueError, OSError, TypeError):
        parser.exit(1, "Recording failed. Check the key, shortlist limits, customer/date and local permissions. "
                    "No credentials or supplier bodies are logged.\n")


if __name__ == "__main__":
    main()
