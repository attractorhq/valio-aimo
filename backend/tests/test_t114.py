"""T114: pack-size wishes, plain-variant preference, "which cream?", "kermajuusto" and context phrases.

All committed tests use synthetic hand-made records (patch P7).
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.agent.parse import LARGE_PACKS, parse_request
from app.agent.plan import CandidateObservation, rank_candidates
from app.agent.suggest import clarification_options
from app.catalog.normalise import normalise_product
from app.contracts import Preference, RequestItem, Requirement
from app.policy import M1
from tests.synthetic.catalog_records import synthetic_product_record

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
MONDAY = date(2026, 11, 16)
LARGE = Preference(kind="OTHER", value=LARGE_PACKS)


def _only(text: str):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    (item,) = result.request.items
    return item, result.request


# --------------------------------------------------------------------------- parsing


def test_judge_butter_request_keeps_salted_and_records_the_large_pack_wish():
    item, request = _only("Tarvitsen 25 kg voita huomiseksi, normaalisuolainen, isoissa pakkauksissa.")
    assert item.what == "butter salted"
    assert item.quantity == Decimal("25") and item.unit == "kg"
    assert item.requirements == []
    assert LARGE in item.preferences
    assert LARGE_PACKS in request.assumptions


def test_pack_size_wishes_in_english_and_finnish_never_reach_the_product_name():
    for text in ("10 kg butter in large packs", "20 kg cheese in catering packs", "10 kg butter, bulk",
                 "suurtalouspakkaus voita 10 kg"):
        item, _ = _only(text)
        assert "pack" not in item.what and "bulk" not in item.what, text
        assert LARGE in item.preferences, text


def test_butter_without_salt_wish_shows_the_choice_as_an_assumption():
    _, request = _only("10 kg butter for Monday")
    assert "Salted or unsalted butter" in request.assumptions
    _, salted = _only("10 kg salted butter for Monday")
    assert "Salted or unsalted butter" not in salted.assumptions


def test_context_phrases_are_never_part_of_the_product_query():
    item, _ = _only("5000 kg of gluten-free and egg-free fresh pasta delivered today by noon for the hospital ward")
    assert item.what == "fresh pasta"
    assert item.requirements == [Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN"),
                                 Requirement(kind="AVOID_ALLERGEN", code="AE")]
    for text in ("fresh pasta 20 kg for the ward", "fresh pasta 20 kg for the canteen", "tuorepastaa 20 kg osastolle"):
        assert _only(text)[0].what == "fresh pasta", text


def test_bare_cream_asks_which_cream_with_one_tap_choices():
    for text in ("20 l kermaa maanantaiksi", "20 l cream for Monday"):
        result = parse_request(text, NOW)
        assert result.clarificationQuestion is not None, text
        options = clarification_options(text, NOW)
        assert [o.label for o in options] == ["Whipping cream", "Cooking cream"]
        for option in options:
            assert parse_request(option.requestText, NOW).clarificationQuestion is None, option
    assert [o.requestText for o in clarification_options("20 l kermaa maanantaiksi", NOW)] == [
        "20 l vispikermaa maanantaiksi", "20 l ruokakermaa maanantaiksi"]


def test_a_typed_cream_is_not_asked_again():
    for text, what in (("20 l vispikermaa maanantaiksi", "whipping cream"), ("20 l cooking cream for Monday",
                       "cooking cream"), ("20 l cream for cooking tomorrow", "cooking cream"),
                       ("5 kg cream cheese for Monday", "cream cheese")):
        assert _only(text)[0].what == what


def test_kermajuusto_asks_semi_hard_or_cream_cheese_and_keeps_requirements():
    result = parse_request("kermajuustoa 5 kg", NOW)
    assert result.clarificationQuestion is not None and "cream cheese" in result.clarificationQuestion
    options = clarification_options("laktoositonta kermajuustoa 5 kg", NOW)
    assert [o.label for o in options] == ["Semi-hard cheese (tilsit)", "Cream cheese"]
    whats = []
    for option in options:
        item, _ = _only(option.requestText)
        assert item.requirements == [Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")]
        whats.append(item.what)
    assert whats == ["tilsit cheese", "cream cheese"]


# --------------------------------------------------------------------------- ranking


def _butter(pid: str, en: str, kg: str, *, claims: list[str] | None = None):
    return normalise_product(synthetic_product_record(
        product_id=pid, category="10501", names={"en": en, "fi": en.replace("butter", "voi")}, claims=claims,
        unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "weight": {"unit": "KGM", "value": kg}}],
    ))


def _obs(pid: str, unit_price: str, per_kg: str) -> CandidateObservation:
    return CandidateObservation(product_id=pid, unit_price_excl_vat=Decimal(unit_price),
                                comparison_price=Decimal(per_kg), comparison_unit="kg", delivery_date=MONDAY)


SMALL = _butter("SYN-B500", "Synthetic butter salted 500 g", "0.5")
BIG = _butter("SYN-B10", "Synthetic butter salted 10 kg", "10")
LACTOSE_FREE_SMALL = _butter("SYN-BLF", "Synthetic lactose free butter 500 g", "0.5", claims=["FREE_FROM_LACTOSE"])


def _rank(item: RequestItem, products, observations):
    return rank_candidates(products, item=item, mandate=M1, requested_date=MONDAY,
                           observations={o.product_id: o for o in observations})


def test_a_large_pack_wish_ranks_the_largest_compliant_pack_first():
    obs = [_obs("SYN-B500", "4.00", "8.00"), _obs("SYN-B10", "82.00", "8.20")]
    plain = RequestItem(itemId="I1", what="butter", quantity=Decimal("25"), unit="kg")
    assert _rank(plain, [SMALL, BIG], obs)[0].productId == "SYN-B500"  # cheapest per kg without the wish
    wish = plain.model_copy(update={"preferences": [LARGE]})
    assert _rank(wish, [SMALL, BIG], obs)[0].productId == "SYN-B10"


def test_an_unrequested_special_variant_loses_to_the_plain_one_at_a_similar_price():
    obs = [_obs("SYN-BLF", "3.95", "7.90"), _obs("SYN-B500", "4.00", "8.00")]
    item = RequestItem(itemId="I1", what="butter", quantity=Decimal("25"), unit="kg")
    assert _rank(item, [LACTOSE_FREE_SMALL, SMALL], obs)[0].productId == "SYN-B500"
    # Much cheaper special variant still wins: a tie-break, not an exclusion.
    cheap = [_obs("SYN-BLF", "3.00", "6.00"), _obs("SYN-B500", "4.00", "8.00")]
    assert _rank(item, [LACTOSE_FREE_SMALL, SMALL], cheap)[0].productId == "SYN-BLF"


def test_a_stated_requirement_keeps_the_special_variant():
    obs = [_obs("SYN-BLF", "4.00", "8.00"), _obs("SYN-B500", "3.00", "6.00")]
    item = RequestItem(itemId="I1", what="butter", quantity=Decimal("25"), unit="kg",
                       requirements=[Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")])
    ranked = _rank(item, [LACTOSE_FREE_SMALL, SMALL], obs)
    assert [c.productId for c in ranked] == ["SYN-BLF"]
