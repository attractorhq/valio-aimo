"""School orders must prove request-wide Finnish exclusions before any confirmation."""

import pytest
from test_agent_run import NOW
from test_agent_run import synthetic_api as synthetic_api

from app.agent.run import AgentRunResult, GatewayAgentChannel, run_agent
from app.contracts import AllergenDeclaration, ReasonCode
from app.policy import M2

SCHOOL_REQUEST = (
    "Koulukeittiö: 60 kg gluteenitonta pastaa ja 30 litraa maidotonta kaurajuomaa maanantaiksi. "
    "Ei saa sisältää pähkinöitä."
)


@pytest.mark.parametrize("explicitly_safe", [False, True])
def test_school_nut_exclusion_is_proven_or_excluded_never_missing(synthetic_api, explicitly_safe):
    client, state = synthetic_api
    safe_ids = {"474235", "468465"}
    if explicitly_safe:
        products = {}
        evidence = state.gateway.products["474235"].allergens[0].evidence
        for code, product in state.gateway.products.items():
            # Hand-made declarations: only two products are explicitly free from all required allergens.
            declarations = [
                AllergenDeclaration(code=allergen, status="FREE_FROM" if code in safe_ids else "CONTAINS",
                                    evidence=evidence)
                for allergen in ("AN", "AP", "AM")
            ]
            products[code] = product.model_copy(update={"allergens": [*product.allergens, *declarations]})
        state.gateway.products = products
        state.catalog = list(products.values())

    response = client.post("/v0.1/agent/run", json={"customerId": M2.customerId, "requestText": SCHOOL_REQUEST})
    assert response.status_code == 200, response.text
    result = AgentRunResult.model_validate(response.json())
    assert result.request is not None and len(result.request.items) == 2
    assert all({"AN", "AP"} <= {r.code for r in item.requirements} for item in result.request.items)
    assert any(note.startswith("Applied to every item:") for note in result.request.assumptions)
    lookup_plan = next(step.plan for step in result.steps if step.name == "LOOKUP")
    assert lookup_plan is not None

    if explicitly_safe:
        assert result.proposal is not None
        assert {line.productId for line in result.proposal.lines} == safe_ids
        assert all({"AN", "AP"} <= {r.code for r in item.requirements}
                   for item in result.proposal.request.items)
        for plan in lookup_plan:
            assert all(candidate.productId in safe_ids for candidate in plan.rankedCandidates)
        for line in result.proposal.lines:
            product = state.gateway.products[line.productId]
            for allergen in ("AN", "AP"):
                assert any(a.code == allergen and a.status == "FREE_FROM" for a in product.allergens)
    else:
        # A STRICT buyer cannot waive an implicit/missing nut declaration by approving the order.
        assert result.proposal is None
        assert all(not plan.rankedCandidates for plan in lookup_plan)
        assert any(reason in {ReasonCode.ALLERGEN_IMPLICIT, ReasonCode.ALLERGEN_UNKNOWN}
                   for plan in lookup_plan for excluded in plan.excluded for reason in excluded.reasons)


def test_unknown_finnish_exclusion_stops_before_lookup(synthetic_api, monkeypatch):
    _client, state = synthetic_api
    channel = GatewayAgentChannel(state)

    def forbidden_lookup(*args, **kwargs):
        pytest.fail("An unmapped exclusion must stop before any lookup or ordering")

    monkeypatch.setattr(channel, "search_products", forbidden_lookup)
    result = run_agent(M2.customerId, SCHOOL_REQUEST + " Ei saa sisältää sokeria.", channel=channel, now=NOW)
    assert result.proposal is None
    assert result.reasons == [ReasonCode.CLARIFICATION_NEEDED]
    assert [step.name for step in result.steps] == ["REQUEST"]
    assert result.clarificationQuestion.count("?") == 1
