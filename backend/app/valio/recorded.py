"""Explicit offline replay of private captures. No network and no live fallback."""

import json
from datetime import date, datetime
from pathlib import Path

from .client import RawBatch, product_ids, subset_batch
from .errors import IntegrationError
from .provider import normalise_batch
from .vendor import aware


class RecordedValioProvider:
    def __init__(self, capture_dir: Path | str):
        self.capture_dir = Path(capture_dir).resolve()
        try:
            self.manifest = json.loads((self.capture_dir / "manifest.json").read_text())
            if self.manifest["version"] != 1 or not isinstance(self.manifest["batches"], list):
                raise ValueError("Unsupported manifest")
            self.recorded_at = aware(datetime.fromisoformat(self.manifest["capturedAt"]))
        except (OSError, ValueError, KeyError, TypeError):
            raise IntegrationError("Recorded capture is missing or invalid; no live fallback was used.") from None

    def _read(self, filename: str) -> bytes:
        path = (self.capture_dir / filename).resolve()
        if not path.is_relative_to(self.capture_dir):
            raise ValueError("Capture file must stay inside its directory")
        return path.read_bytes()

    def fetch(self, shortlists, requested_date: date, customer_id: str, *, force_refresh=False) -> RawBatch:
        # force_refresh deliberately does not advance the fixture clock or consult the network.
        ids = product_ids(shortlists)
        try:
            for entry in self.manifest["batches"]:
                if (entry["customerId"] != customer_id or entry["requestedDate"] != requested_date.isoformat()
                        or not set(ids).issubset(entry["productIds"])):
                    continue
                batch = RawBatch(
                    customer_id, entry["upstreamCustomerId"], requested_date, tuple(entry["productIds"]),
                    self._read(entry["priceFile"]), self._read(entry["availabilityFile"]),
                    aware(datetime.fromisoformat(entry["priceRetrievedAt"])), self.recorded_at,
                    "RECORDED", self.recorded_at,
                )
                batch.envelopes()
                return subset_batch(batch, ids)
        except (OSError, ValueError, KeyError, TypeError):
            raise IntegrationError("Recorded batch is invalid; no live fallback was used.") from None
        raise IntegrationError("No recorded batch matches this customer, date and shortlist; no live fallback used.")

    def lookup(self, shortlists, requested_date, customer_id, *, products=None, force_refresh=False):
        return normalise_batch(self.fetch(shortlists, requested_date, customer_id, force_refresh=force_refresh), products)
