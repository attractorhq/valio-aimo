"""T10 unit tests for `backend/app/agent/explain.py`: <= 2 sentences, LLM + template fallback, AC15 guard."""

import time

import pytest
from test_proposals_gateway import PRODUCTS, MutableSyntheticProvider, request_a, request_b, request_c

from app.agent.explain import (
    clamp_to_two_sentences,
    contains_forbidden_agent_content,
    count_sentences,
    explain_proposal,
    make_openrouter_explainer,
)
from app.contracts import ReasonCode
from app.gateway.proposals import ProposalGateway
from app.policy import M1, M2


@pytest.fixture
def proposals():
    provider = MutableSyntheticProvider()
    gateway = ProposalGateway(products=PRODUCTS, provider=provider)

    offer_a, req_a = request_a()
    prop_a = gateway.create_proposal(M1.customerId, gateway.get_offer(offer_a).offer.offerId, 1, req_a)

    offer_b, req_b = request_b()
    prop_b = gateway.create_proposal(M2.customerId, gateway.get_offer(offer_b).offer.offerId, 1, req_b)

    offer_c, req_c = request_c()
    prop_c = gateway.create_proposal(M1.customerId, gateway.get_offer(offer_c).offer.offerId, 1, req_c)

    return prop_a, prop_b, prop_c


def test_template_explanations_for_a_b_c_are_at_most_two_sentences(proposals):
    prop_a, prop_b, prop_c = proposals

    exp_a = explain_proposal(proposal=prop_a, mandate=M1)
    assert exp_a.mode == "TEMPLATE"
    assert 1 <= exp_a.sentenceCount <= 2
    assert count_sentences(exp_a.text) <= 2
    assert "€1,010.00" in exp_a.text
    assert not contains_forbidden_agent_content(exp_a.text)

    exp_b = explain_proposal(proposal=prop_b, mandate=M2)
    assert exp_b.mode == "TEMPLATE"
    assert 1 <= exp_b.sentenceCount <= 2
    assert count_sentences(exp_b.text) <= 2
    assert "€506.16" in exp_b.text
    assert "€500.00" in exp_b.text
    assert "SYN-REJECTED-SUB" in exp_b.text
    assert [c.code for c in exp_b.reasonChips] == [ReasonCode.OVER_AUTO_LIMIT]
    assert not contains_forbidden_agent_content(exp_b.text)

    exp_c = explain_proposal(proposal=prop_c, mandate=M1)
    assert exp_c.mode == "TEMPLATE"
    assert 1 <= exp_c.sentenceCount <= 2
    assert count_sentences(exp_c.text) <= 2
    assert "€20,200.00" in exp_c.text
    assert "€5,000.00" in exp_c.text
    assert "ignored" in exp_c.text.lower()
    assert [c.code for c in exp_c.reasonChips] == [ReasonCode.OVER_HARD_LIMIT, ReasonCode.INSTRUCTION_IGNORED]
    assert not contains_forbidden_agent_content(exp_c.text)


def test_no_order_clarification_and_no_candidate_explanations():
    exp_clar = explain_proposal(
        reasons=[ReasonCode.INSTRUCTION_IGNORED, ReasonCode.CLARIFICATION_NEEDED],
        clarification_question="How much milk do you need, in litres, kilograms, pieces or cases?",
        mandate=M1,
    )
    assert 1 <= exp_clar.sentenceCount <= 2
    assert "How much milk do you need" in exp_clar.text

    exp_no_cand = explain_proposal(
        reasons=[ReasonCode.NO_CANDIDATE],
        mandate=M2,
    )
    assert 1 <= exp_no_cand.sentenceCount <= 2
    assert "assortment" in exp_no_cand.text.lower()


def test_llm_explainer_is_used_and_clamped_to_two_sentences(proposals):
    _, prop_b, _ = proposals

    def verbose_llm(_payload: dict) -> str:
        return (
            "Everything is proven for Monday at €506.16 excl. VAT, which is €6.16 above the €500.00 auto-approve limit. "
            "Proposal v1 waits for Head Chef, while substitute SYN-REJECTED-SUB was rejected. "
            "This third sentence must be stripped by the two-sentence clamp."
        )

    res = explain_proposal(proposal=prop_b, mandate=M2, explainer=verbose_llm)
    assert res.mode == "LLM"
    assert res.fallbackReason is None
    assert res.sentenceCount == 2
    assert count_sentences(res.text) == 2
    assert "third sentence" not in res.text


@pytest.mark.parametrize(
    "bad_output",
    [
        "",
        "   ",
        "Approve here: https://example.com/approve/atk-12345",
        "Open /approver/proposals/PR-1 to sign off.",
        "Use approvalToken atk-secret-999.",
    ],
)
def test_llm_explainer_rejects_empty_or_credential_leaking_output_and_falls_back(proposals, bad_output):
    _, prop_b, _ = proposals
    res = explain_proposal(proposal=prop_b, mandate=M2, explainer=lambda _p: bad_output)
    assert (res.mode, res.fallbackReason) == ("TEMPLATE", "invalid_output")
    assert not contains_forbidden_agent_content(res.text)
    assert 1 <= count_sentences(res.text) <= 2


def test_slow_or_failing_llm_explainer_falls_back_quickly(proposals):
    prop_a, _, _ = proposals

    def slow_llm(_payload: dict) -> str:
        time.sleep(4)
        return "Confirmed."

    t0 = time.perf_counter()
    res_timeout = explain_proposal(proposal=prop_a, mandate=M1, explainer=slow_llm, deadline_s=0.2)
    assert time.perf_counter() - t0 < 1.5
    assert (res_timeout.mode, res_timeout.fallbackReason) == ("TEMPLATE", "timeout")

    def failing_llm(_payload: dict) -> str:
        raise RuntimeError("OpenRouter down")

    res_err = explain_proposal(proposal=prop_a, mandate=M1, explainer=failing_llm)
    assert (res_err.mode, res_err.fallbackReason) == ("TEMPLATE", "llm_error")


def test_clamp_to_two_sentences_preserves_decimals_and_abbreviations():
    raw = (
        "Order total is €506.16 excl. VAT for product no. 474235. "
        "Version 1 waits for Head Chef incl. VAT €574.49. "
        "Extra sentence."
    )
    clamped = clamp_to_two_sentences(raw)
    assert count_sentences(clamped) == 2
    assert "€506.16 excl. VAT" in clamped
    assert "no. 474235" in clamped
    assert "Extra sentence" not in clamped


def test_make_openrouter_explainer_requires_api_key():
    assert make_openrouter_explainer("", "z-ai/glm-5.3-flash") is None
    assert callable(make_openrouter_explainer("sk-test", "z-ai/glm-5.3-flash"))
