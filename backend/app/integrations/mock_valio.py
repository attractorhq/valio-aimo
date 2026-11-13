from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.integrations.provider import AvailabilityCheck, IntegrationError, PriceCheck


def helsinki_today() -> date:
    return datetime.now(ZoneInfo("Europe/Helsinki")).date()


class MockValioProvider:
    mock = True
    prices = {
        "DEMO-MLK-01": Decimal("1.45"),
        "DEMO-MLK-02": Decimal("1.39"),
        "DEMO-OAT-01": Decimal("1.85"),
        "DEMO-MLK-03": Decimal("1.65"),
        "DEMO-MLK-04": Decimal("1.29"),
        "DEMO-VEG-01": Decimal("2.20"),
        "DEMO-RIC-01": Decimal("2.10"),
        "DEMO-SUP-01": Decimal("3.90"),
    }
    stock = {code: Decimal("5000") for code in prices}
    stock["DEMO-MLK-04"] = Decimal("0")

    def __init__(self):
        self.prices = dict(type(self).prices)
        self.stock = dict(type(self).stock)

    def search_products(self, products, query):
        terms = query.lower().replace("lactose-free", "lactose free").split()
        return [
            p
            for p in products
            if all(
                term in f"{p.name} {p.code} {p.category}".lower().replace("lactose-free", "lactose free")
                for term in terms
            )
        ]

    def get_prices(self, codes, delivery_date=None, buyer_id=None):
        if any(code not in self.prices for code in codes):
            raise IntegrationError(
                "No synthetic pricing fixture for imported product. Configure a verified live adapter."
            )
        return {
            code: PriceCheck(self.prices[code], "EUR", "Synthetic fixed-price simulator", datetime.now(UTC))
            for code in codes
        }

    def check_availability(self, codes, delivery_date, buyer_id=None):
        if any(code not in self.stock for code in codes):
            raise IntegrationError("No synthetic availability fixture for imported product.")
        return {
            code: AvailabilityCheck(
                self.stock[code],
                helsinki_today() + timedelta(days=1),
                "Synthetic stock simulator",
                datetime.now(UTC),
            )
            for code in codes
        }
