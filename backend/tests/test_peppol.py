"""T72: a confirmed proposal downloads as a well-formed Peppol BIS Order (UBL 2.1); nothing is sent anywhere.
Synthetic hand-made records only (patch P7)."""

from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from test_api import api, scenario_a, scenario_b  # noqa: F401

from app.gateway.peppol import CAC, CBC, ORDER_RESPONSE_CODES, PROFILE_ID, SIMULATED_NOTE, UBL
from app.main import app
from app.policy import M1, M2

HOTEL = {"Authorization": f"Bearer demo-agent-{M1.customerId}"}
SCHOOL = {"Authorization": f"Bearer demo-agent-{M2.customerId}"}
NS = {"ubl": UBL, "cac": CAC, "cbc": CBC}


@pytest.fixture
def raw(api):  # noqa: F811
    authenticated, _, state = api
    _, confirmed = scenario_a(authenticated)
    _, pending = scenario_b(authenticated)
    with TestClient(app) as client:
        yield client, confirmed.json(), pending.json(), state


def _order(client, proposal_id, headers=HOTEL):
    return client.get(f"/v0.1/proposals/{proposal_id}/peppol-order", headers=headers)


def test_a_confirmed_proposal_is_a_well_formed_peppol_order(raw):
    client, confirmed, _, state = raw
    assert confirmed["state"] == "CONFIRMED_SIMULATED"
    state.gateway.products["SYN-MILK"] = state.gateway.products["SYN-MILK"].model_copy(
        update={"gtin": "06400000000017"})
    response = _order(client, confirmed["proposalId"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert "attachment" in response.headers["content-disposition"]

    root = ET.fromstring(response.content)  # well-formed
    assert root.tag == f"{{{UBL}}}Order"
    assert root.findtext("cbc:ProfileID", namespaces=NS) == PROFILE_ID
    assert root.findtext("cbc:ID", namespaces=NS) == confirmed["proposalId"]
    assert root.findtext("cbc:Note", namespaces=NS) == SIMULATED_NOTE
    assert root.findtext("cac:SellerSupplierParty/cac:Party/cac:PartyName/cbc:Name", namespaces=NS) == "Valio Aimo"
    assert root.findtext("cac:BuyerCustomerParty/cac:Party/cac:PartyName/cbc:Name", namespaces=NS) == (
        M1.organisationName)

    lines = root.findall("cac:OrderLine/cac:LineItem", NS)
    assert len(lines) == len(confirmed["lines"])
    for xml_line, line in zip(lines, confirmed["lines"], strict=True):
        assert xml_line.findtext("cbc:ID", namespaces=NS) == line["lineId"]
        assert xml_line.find("cbc:Quantity", NS).text == line["quantitySalesUnits"]
        assert xml_line.findtext("cbc:LineExtensionAmount", namespaces=NS) == line["lineTotalExclVat"]
        assert xml_line.findtext("cac:Price/cbc:PriceAmount", namespaces=NS) == line["unitPriceExclVat"]
        assert xml_line.findtext("cac:Item/cac:SellersItemIdentification/cbc:ID", namespaces=NS) == line["productId"]
    gtin = lines[0].find("cac:Item/cac:StandardItemIdentification/cbc:ID", NS)
    assert (gtin.text, gtin.get("schemeID")) == ("06400000000017", "0160")
    assert root.findtext("cac:AnticipatedMonetaryTotal/cbc:LineExtensionAmount", namespaces=NS) == (
        confirmed["totals"]["exclVat"])


def test_only_a_confirmed_order_has_an_order_document(raw):
    client, _, pending, _ = raw
    assert pending["state"] == "PENDING_APPROVAL"
    response = _order(client, pending["proposalId"], headers=SCHOOL)
    assert response.status_code == 409


def test_the_order_is_scoped_like_the_proposal(raw):
    client, confirmed, _, _ = raw
    assert _order(client, confirmed["proposalId"], headers={}).status_code == 401
    assert _order(client, confirmed["proposalId"], headers=SCHOOL).status_code == 404


def test_every_state_maps_to_an_order_response_code():
    assert {code for _, code, _, _ in ORDER_RESPONSE_CODES} == {"AP", "CA", "RE"}
    assert {line for _, _, line, _ in ORDER_RESPONSE_CODES} == {"5", "3", "7"}
