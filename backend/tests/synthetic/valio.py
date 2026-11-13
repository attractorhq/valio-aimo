"""Hand-made upstream records. Not copied from the event dataset or API recordings."""

from datetime import date, datetime

NOW = datetime.fromisoformat("2026-11-13T12:00:00+02:00")
DAY = date(2026, 11, 16)


def price_row(code="SYN-A"):
    def price(value):
        return {"value": value, "valueWithTax": value * 1.135, "currency": "EUR",
                "validFrom": "2026-01-01T00:00:00+02:00", "validTo": "2026-12-31T23:59:59+02:00"}

    return {
        "productId": code, "quantity": 1, "catalogPrice": price(2.5),
        "comparisonPrice": price(1.25), "comparisonUnit": "L", "listPrice": price(3),
        "tax": {"taxRate": 13.5, "taxValue": 0.3375}, "netPriceFound": True, "error": False,
    }


def availability_row(code="SYN-A"):
    return {
        "productId": code, "closingTime": "2026-11-15T17:00:00+02:00", "salesBlock": False,
        "logisticModel": "Stock", "scheduleLines": [{
            "salesBlock": False, "availableAmount": 24, "availableDate": DAY.isoformat(), "salesUnit": "ST",
        }],
    }


def envelopes(codes=("SYN-A",), customer="UPSTREAM-A", day=DAY):
    return (
        {"customerId": customer, "currency": "EUR", "pricingDate": f"{day}T00:00:00+02:00",
         "products": [price_row(code) for code in codes]},
        {"timeNow": NOW.isoformat(), "requestedDate": f"{day}T00:00:00+02:00",
         "products": [availability_row(code) for code in codes]},
    )
