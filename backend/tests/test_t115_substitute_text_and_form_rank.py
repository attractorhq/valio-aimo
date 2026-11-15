"""T115: readable contradiction question, AGENT_SUBSTITUTE detail names the difference, same-form substitutes first."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.agent.parse import parse_request
from app.agent.plan import CandidateObservation, rank_candidates
from app.catalog.normalise import normalise_product
from app.contracts import Preference, ReasonCode, RequestItem, Requirement
from app.policy import M1
from app.policy.lines import substitute_detail
from tests.synthetic.catalog_records import synthetic_product_record

MORNING = datetime(2026, 11, 15, 8, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
MONDAY = date(2026, 11, 16)
LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")


def _product(product_id: str, category: str, en: str, fi: str, *, grams: int = 1000, allergens=None, claims=None):
    return normalise_product(
        synthetic_product_record(
            product_id=product_id,
            category=category,
            names={"en": en, "fi": fi},
            allergens=allergens if allergens is not None else [("AM", "CONTAINS")],
            claims=claims if claims is not None else ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
            unit_conversions=[
                {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": grams}}
            ],
        )
    )


def _obs(product_id: str, comp_price: str) -> CandidateObservation:
    return CandidateObservation(
        product_id=product_id,
        unit_price_excl_vat=Decimal(comp_price),
        comparison_price=Decimal(comp_price),
        comparison_unit="kg",
        vat_rate=Decimal("14"),
        stock_tier="PLENTY",
        available_quantity=Decimal("1000"),
        delivery_date=MONDAY,
    )


# --------------------------------------------------------------------------- contradiction question


def test_vegan_cream_made_from_cow_milk_is_asked_from_parsed_parts():
    result = parse_request(
        "Need 50 litres of vegan whipping cream made from real cow milk, lactose-free, delivered today by 10am.",
        MORNING,
    )
    assert ReasonCode.CLARIFICATION_NEEDED in result.reasons
    assert result.clarificationQuestion == (
        "Vegan and made from cow's milk cannot both hold. Which do you need: a plant-based whipping cream, "
        "or a lactose-free dairy whipping cream?"
    )


def test_contradiction_question_never_splices_leftover_request_words():
    question = parse_request("10 kg milk-free cheddar made from genuine goat milk tomorrow", MORNING).clarificationQuestion
    assert question.startswith("Milk-free and made from goat's milk cannot both hold.")
    assert "genuine" not in question
    assert "made from milk" not in question
    assert question.count("?") == 1


# --------------------------------------------------------------------------- AGENT_SUBSTITUTE detail


def test_substitute_detail_names_the_fat_difference_and_what_it_proves():
    cream = _product("SYN-CREAM-38", "10300", "Synthetic lactose-free whipping cream 38 % 1 l",
                     "Synteettinen laktoositon vispikerma 38 % 1 l", claims=["FREE_FROM_LACTOSE"])
    item = RequestItem(itemId="I1", what="whipping cream", quantity=Decimal("30"), unit="l",
                       requirements=[LACTOSE_FREE], preferences=[Preference(kind="FAT_PERCENT", value="35")])
    detail = substitute_detail(cream, item, allergens=("milk",))
    assert detail == (
        "Synthetic lactose-free whipping cream 38 % 1 l: 38 % fat instead of 35 %; proves lactose-free; contains milk"
    )


def test_substitute_detail_is_never_only_the_allergen():
    cream = _product("SYN-CREAM", "10300", "Synthetic lactose-free whipping cream 1 l",
                     "Synteettinen laktoositon vispikerma 1 l", claims=["FREE_FROM_LACTOSE"])
    item = RequestItem(itemId="I1", what="whipping cream", quantity=Decimal("30"), unit="l",
                       requirements=[LACTOSE_FREE], preferences=[Preference(kind="FAT_PERCENT", value="35")])
    detail = substitute_detail(cream, item, allergens=("milk",))
    assert "fat % not stated (35 % asked)" in detail
    assert "proves lactose-free" in detail
    assert detail != "Contains milk"
    plain = RequestItem(itemId="I1", what="whipping cream", quantity=Decimal("30"), unit="l")
    assert "not an exact match for the whipping cream asked" in substitute_detail(cream, plain, allergens=("milk",))


# --------------------------------------------------------------------------- same form ranks first


def test_same_form_substitute_ranks_before_a_cheaper_different_form():
    block = _product("SYN-EMM-BLOCK", "10600", "Synthetic Emmental cheese 625 g", "Synteettinen emmental 625 g",
                     grams=625)
    grated_edam = _product("SYN-EDAM-GRATED", "10603", "Synthetic Edam grated cheese 2500 g",
                           "Synteettinen edam raaste 2500 g", grams=2500)
    item = RequestItem(itemId="I1", what="grated Emmental cheese", quantity=Decimal("15"), unit="kg",
                       requirements=[LACTOSE_FREE, GLUTEN_FREE], preferences=[Preference(kind="OTHER", value="Grated")])
    ranked = rank_candidates(
        [block, grated_edam], item=item, mandate=M1, requested_date=MONDAY,
        observations={"SYN-EMM-BLOCK": _obs("SYN-EMM-BLOCK", "6.00"),
                      "SYN-EDAM-GRATED": _obs("SYN-EDAM-GRATED", "9.00")},
    )
    assert [c.productId for c in ranked] == ["SYN-EDAM-GRATED", "SYN-EMM-BLOCK"]
    assert all(c.substitutionSource == "AGENT" for c in ranked)


