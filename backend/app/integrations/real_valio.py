from app.integrations.provider import IntegrationError


class RealValioProvider:
    """Fail closed until authenticated OpenAPI is available. Never guesses vendor routes."""

    mock = False

    def search_products(self, products, query):
        terms = query.casefold().split()
        return [p for p in products if all(t in f"{p.name} {p.code} {p.category}".casefold() for t in terms)]

    def get_prices(self, codes):
        raise IntegrationError(
            "Live pricing adapter disabled: authenticated Valio OpenAPI contract not yet verified."
        )

    def check_availability(self, codes, delivery_date):
        raise IntegrationError(
            "Live availability adapter disabled: authenticated Valio OpenAPI contract not yet verified."
        )
