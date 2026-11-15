"""T72: a confirmed proposal as a minimal Peppol BIS Order (UBL 2.1 Order), and the Order Response code mapping.

The path to production for Finnish food wholesale runs through Peppol. The document is built only from the
proposal's own frozen lines and the catalogue; nothing is sent anywhere. Orders are simulated, so the document
says so in `cbc:Note`.
"""

from collections.abc import Mapping
from decimal import Decimal
from xml.etree import ElementTree as ET

from app.contracts import Mandate, Product, Proposal

UBL = "urn:oasis:names:specification:ubl:schema:xsd:Order-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CUSTOMIZATION_ID = "urn:fdc:peppol.eu:poacc:trns:order:3"
PROFILE_ID = "urn:fdc:peppol.eu:poacc:bis:ordering:3"
SIMULATED_NOTE = "Simulated order — mock environment"
SELLER_NAME = "Valio Aimo"
GTIN_SCHEME = "0160"  # ISO 6523 ICD for GS1 GTIN

# UN/ECE Recommendation 20 unit codes for our sales units; any other unit is a countable piece.
_UNIT_CODES = {"KG": "KGM", "KGM": "KGM", "L": "LTR", "LTR": "LTR"}

# How our proposal states map to a Peppol BIS Order Response (document ResponseCode, UNCL4343 subset) and to the
# line status (UNCL1229): 5 accepted without amendment, 3 changed, 7 not accepted.
ORDER_RESPONSE_CODES: tuple[tuple[str, str, str, str], ...] = (
    ("CONFIRMED_SIMULATED", "AP", "5", "Accepted: every line exactly as proposed."),
    ("PENDING_APPROVAL (trade-off: partial, later date, substitute)", "CA", "3",
     "Conditionally accepted: the changed lines wait for the buyer's sign-off."),
    ("SUPERSEDED (re-check found a change)", "CA", "3", "A new version with the changed lines replaces it."),
    ("REJECTED / BLOCKED / EXPIRED", "RE", "7", "Rejected: nothing is ordered."),
)

ET.register_namespace("", UBL)
ET.register_namespace("cac", CAC)
ET.register_namespace("cbc", CBC)


def _cbc(parent: ET.Element, tag: str, text: object, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, f"{{{CBC}}}{tag}", attrs)
    element.text = str(text)
    return element


def _cac(parent: ET.Element, tag: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{CAC}}}{tag}")


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def _party(parent: ET.Element, role: str, name: str, party_id: str | None = None) -> None:
    party = _cac(_cac(parent, role), "Party")
    if party_id:
        _cbc(_cac(party, "PartyIdentification"), "ID", party_id)
    _cbc(_cac(party, "PartyName"), "Name", name)


def peppol_order_xml(proposal: Proposal, *, mandate: Mandate | None,
                     products: Mapping[str, Product]) -> str:
    """The proposal's lines as a UBL 2.1 Order following the Peppol BIS Ordering 3 profile."""
    order = ET.Element(f"{{{UBL}}}Order")
    _cbc(order, "CustomizationID", CUSTOMIZATION_ID)
    _cbc(order, "ProfileID", PROFILE_ID)
    _cbc(order, "ID", proposal.proposalId)
    _cbc(order, "IssueDate", proposal.createdAt.date().isoformat())
    _cbc(order, "IssueTime", proposal.createdAt.strftime("%H:%M:%S"))
    _cbc(order, "Note", SIMULATED_NOTE)
    _cbc(order, "DocumentCurrencyCode", proposal.totals.currency)
    _cbc(order, "CustomerReference", f"{proposal.offerId} v{proposal.offerVersion}")

    buyer = mandate.organisationName if mandate else proposal.customerId
    _party(order, "BuyerCustomerParty", buyer, proposal.customerId)
    _party(order, "SellerSupplierParty", SELLER_NAME)

    days = sorted({line.deliveryDate for line in proposal.lines}) or [proposal.requestedDate]
    period = _cac(_cac(order, "Delivery"), "RequestedDeliveryPeriod")
    _cbc(period, "StartDate", days[0].isoformat())
    _cbc(period, "EndDate", days[-1].isoformat())

    totals = _cac(order, "AnticipatedMonetaryTotal")
    _cbc(totals, "LineExtensionAmount", _money(proposal.totals.exclVat), currencyID=proposal.totals.currency)
    _cbc(totals, "PayableAmount", _money(proposal.totals.inclVat), currencyID=proposal.totals.currency)

    for line in proposal.lines:
        item_line = _cac(_cac(order, "OrderLine"), "LineItem")
        _cbc(item_line, "ID", line.lineId)
        unit = _UNIT_CODES.get(line.salesUnit.upper(), "H87")
        _cbc(item_line, "Quantity", f"{line.quantitySalesUnits.normalize():f}", unitCode=unit)
        _cbc(item_line, "LineExtensionAmount", _money(line.lineTotalExclVat), currencyID=proposal.totals.currency)
        line_period = _cac(_cac(item_line, "Delivery"), "RequestedDeliveryPeriod")
        _cbc(line_period, "StartDate", line.deliveryDate.isoformat())
        _cbc(line_period, "EndDate", line.deliveryDate.isoformat())
        _cbc(_cac(item_line, "Price"), "PriceAmount", f"{line.unitPriceExclVat}", currencyID=proposal.totals.currency)
        item = _cac(item_line, "Item")
        _cbc(item, "Name", line.name)
        _cbc(_cac(item, "SellersItemIdentification"), "ID", line.productId)
        product = products.get(line.productId)
        if product is not None and product.gtin:
            _cbc(_cac(item, "StandardItemIdentification"), "ID", product.gtin, schemeID=GTIN_SCHEME)
        tax = _cac(item, "ClassifiedTaxCategory")
        _cbc(tax, "ID", "S")
        _cbc(tax, "Percent", f"{line.vatRate.normalize():f}")
        _cbc(_cac(tax, "TaxScheme"), "ID", "VAT")

    ET.indent(order)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(order, encoding="unicode") + "\n"
