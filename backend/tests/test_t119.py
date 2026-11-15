"""T119 (judge run 20261115-084227): sweetened whipped cream is never whipping cream, and the request text's own
free-from words cannot be dropped by a structured request. Synthetic records only (patch P7)."""

from datetime import date
from decimal import Decimal

from test_api import api, item, offer_and_propose  # noqa: F401

from app.agent.parse import with_text_requirements
from app.catalog.categories import proposal_type_match
from app.contracts import RequestItem, StructuredRequest
from app.policy import M1, M2

SPRAY = ("Synthetic whipped cream with sugar 250 g", "Synteettinen sokeroitu kermavaahto spray 250 g")
WHIPPING = ("Synthetic whipping cream 1 l lactose free", "Synteettinen vispikerma 1 l laktoositon")


def _match(query, names):
    return proposal_type_match(query, names, category="10401", requirement_codes=())


def test_ready_whipped_cream_is_never_whipping_cream_or_plain_cream():
    assert "ready-whipped cream, not whipping cream" in _match("whipping cream", SPRAY).differences
    assert _match("kuohukerma", SPRAY).exact is False
    assert _match("cream", SPRAY).differences == ("ready-whipped cream, not cream",)
    assert _match("whipping cream", WHIPPING).exact and _match("cream", WHIPPING).exact
    # Asked for by name, the spray is the exact product and whipping cream is not.
    assert _match("whipped cream", SPRAY).exact and _match("kermavaahto", SPRAY).exact
    assert _match("whipped cream", WHIPPING).differences == ("whipping cream, not ready-whipped cream",)


def _request(text, *items):
    return StructuredRequest(requestText=text, requestedDate=date(2026, 11, 16), items=list(items))


def _item(what, item_id="I1"):
    return RequestItem(itemId=item_id, what=what, quantity=Decimal(20), unit="kg")


def test_a_free_from_word_in_the_text_is_added_back_to_the_item_it_names():
    request, added = with_text_requirements(_request("gluten-free flour for coeliac pupils", _item("wheat flour")))
    assert [r.code for r in added] == ["FREE_FROM_GLUTEN"]
    assert [r.code for r in request.items[0].requirements] == ["FREE_FROM_GLUTEN"]
    assert request.assumptions == ["From the request text: gluten-free (missing from the structured request)"]
    # A product id the text never names still gets it (one item).
    _, added = with_text_requirements(_request("gluten-free flour for coeliac pupils", _item("460782")))
    assert [r.code for r in added] == ["FREE_FROM_GLUTEN"]


def test_a_phrase_about_another_item_or_a_kept_reading_adds_nothing():
    # The milk line was dropped from this version: "lactose-free milk" is not about the oat drink.
    text = "We need 50 litres of gluten-free oat drink and 10 litres of lactose-free milk by 20.11."
    oat = RequestItem(itemId="I1", what="oat drink", quantity=Decimal(50), unit="l",
                      requirements=[{"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}])
    assert with_text_requirements(_request(text, oat))[1] == []
    # "nut allergy" read as tree nuts only is a reading, not a drop.
    nuts = RequestItem(itemId="I1", what="pasta", quantity=Decimal(20), unit="kg",
                       requirements=[{"kind": "AVOID_ALLERGEN", "code": "AN"}])
    assert with_text_requirements(_request("20 kg pasta, one pupil has a nut allergy", nuts))[1] == []
    assert with_text_requirements(_request("20 kg flour for the bakery", _item("flour")))[1] == []


def test_a_structured_request_cannot_drop_gluten_free_and_order_the_wrong_flour(api):  # noqa: F811
    """The judge's bypass: an offer for a product without the claim, a request text that says gluten-free and
    coeliac, and structured items without FREE_FROM_GLUTEN. Never AUTO."""
    _, proposal = offer_and_propose(
        api[0], M2.customerId, [("I1", "SYN-MILK", "20", "l")],
        "20 litres of gluten-free milk drink for coeliac pupils by Monday.", [item("I1", "milk drink", "20", "l")],
    )
    body = proposal.json()
    assert body.get("decision") != "AUTO" and body.get("state") != "CONFIRMED_SIMULATED"


def test_the_same_request_with_the_requirement_kept_is_unchanged(api):  # noqa: F811
    _, proposal = offer_and_propose(
        api[0], M1.customerId, [("I1", "SYN-MILK", "1000", "l")],
        "1,000 litres of lactose-free milk drink by Monday.",
        [item("I1", "milk drink", "1000", "l", [{"kind": "CLAIM", "code": "FREE_FROM_LACTOSE"}])],
    )
    body = proposal.json()
    assert body["decision"] == "AUTO" and not any(a.startswith("From the request text") for a in
                                                  body["request"]["assumptions"])
