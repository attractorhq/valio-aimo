"""Request-wide dietary constraints survive both deterministic and LLM interpretation."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import parse_request
from app.contracts import ReasonCode, Requirement

NOW = datetime(2026, 11, 14, 9, tzinfo=ZoneInfo("Europe/Helsinki"))
BASE_FI = "60 kg pastaa ja 30 litraa kaurajuomaa maanantaiksi. "
BASE_EN = "60 kg pasta and 30 litres oat drink by Monday. "
NUTS = {"AN", "AP"}  # General "nut" / Finnish "pähkinä" covers tree nuts and peanuts (T40, T99).


@pytest.mark.parametrize(("base", "exclusion", "codes"), [
    (BASE_FI, "Ei saa sisältää pähkinöitä.", NUTS),
    (BASE_FI, "Ei saa sisältää maapähkinöitä.", {"AP"}),
    (BASE_FI, "Ei saa sisältää gluteenia tai maitoa.", {"FREE_FROM_GLUTEN", "AM"}),
    (BASE_FI, "Ei saa sisältää maitoa, kananmunia eikä seesamia.", {"AM", "AE", "AS"}),
    (BASE_FI, "Ilman pähkinöitä.", NUTS),
    (BASE_FI, "Ilman maitoa ja kananmunia.", {"AM", "AE"}),
    (BASE_FI, "Maitoa ei saa olla.", {"AM"}),
    (BASE_FI, "Gluteenia tai maitoa ei saa olla.", {"FREE_FROM_GLUTEN", "AM"}),
    (BASE_FI, "Täysin pähkinätön.", NUTS),
    (BASE_FI, "Kananmunaton.", {"AE"}),
    (BASE_FI, "Maidoton ja gluteeniton.", {"AM", "FREE_FROM_GLUTEN"}),
    (BASE_FI, "Ei saa sisältää kalaa, soijaa tai sinappia.", {"AF", "AY", "BM"}),
    (BASE_FI, "Ei saa sisältää selleriä eikä lupiineja.", {"BC", "NL"}),
    (BASE_FI, "Ei saa sisältää äyriäisiä tai nilviäisiä.", {"AC", "UM"}),
    (BASE_FI, "Ei saa sisältää vehnää, sulfiitteja tai laktoosia.", {"UW", "AU", "FREE_FROM_LACTOSE"}),
    (BASE_FI, "Koulun keliaakikoille.", {"FREE_FROM_GLUTEN"}),
    (BASE_FI, "Keliaakikko.", {"FREE_FROM_GLUTEN"}),
    (BASE_FI, "Lapsella on keliakia.", {"FREE_FROM_GLUTEN"}),
    (BASE_FI, "Keliakistille.", {"FREE_FROM_GLUTEN"}),
    (BASE_FI, "Oppilaalla on gluteeniallergia.", {"FREE_FROM_GLUTEN"}),
    (BASE_FI, "Potilailla munaallergia.", {"AE"}),
    (BASE_FI, "Potilaalla kananmuna-allergia.", {"AE"}),
    (BASE_FI, "Yksi oppilaista on maitoallerginen.", {"AM"}),
    (BASE_FI, "Lapsella on maitoallergia.", {"AM"}),
    (BASE_FI, "Ryhmällä pähkinäallergia.", NUTS),
    (BASE_FI, "Lapsella on laktoosi-intoleranssi.", {"FREE_FROM_LACTOSE"}),
    (BASE_FI, "Maidottomalle.", {"AM"}),
    (BASE_EN, "No nuts.", NUTS),
    (BASE_EN, "No tree nuts.", {"AN"}),
    (BASE_EN, "No peanuts.", {"AP"}),
    (BASE_EN, "No milk or eggs.", {"AM", "AE"}),
    (BASE_EN, "Without peanuts, tree nuts or sesame.", {"AP", "AN", "AS"}),
    (BASE_EN, "Without any traces of milk.", {"AM"}),
    (BASE_EN, "For celiacs.", {"FREE_FROM_GLUTEN"}),
    (BASE_EN, "For a coeliac.", {"FREE_FROM_GLUTEN"}),
    (BASE_EN, "For milk-allergic children.", {"AM"}),
])
def test_request_wide_exclusions_apply_to_every_item_and_are_visible(base, exclusion, codes):
    result = parse_request(base + exclusion, NOW)
    assert result.clarificationQuestion is None
    assert result.reasons == []
    assert len(result.request.items) == 2
    for item in result.request.items:
        assert {r.code for r in item.requirements} == codes
        assert all(r.kind == ("CLAIM" if r.code.startswith("FREE_FROM_") else "AVOID_ALLERGEN")
                   for r in item.requirements)
    assert any(note.startswith("Applied to every item:") for note in result.request.assumptions)


@pytest.mark.parametrize("text", [
    "Koulukeittiö: 60 kg gluteenitonta pastaa ja 30 litraa maidotonta kaurajuomaa maanantaiksi. "
    "Ei saa sisältää pähkinöitä.",
    "Tarvitsemme huomiseksi 40 kg gluteenitonta ja maidotonta pastaa sekä 20 litraa kauramaitoa "
    "koulun keliaakikoille. Ei saa sisältää gluteenia tai maitoa.",
])
def test_finnish_school_request_keeps_local_and_global_requirements(text):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None
    assert len(result.request.items) == 2
    codes = [{r.code for r in item.requirements} for item in result.request.items]
    if "pähkinöitä" in text:
        assert codes == [{"FREE_FROM_GLUTEN", *NUTS}, {"AM", *NUTS}]
    else:
        assert codes == [{"FREE_FROM_GLUTEN", "AM"}] * 2


@pytest.mark.parametrize("exclusion", [
    "Ei saa sisältää sokeria.", "Ei saa sisältää maitoa tai sokeria.",
    "Ei saa sisältää pähkinöitä, sokeria eikä maitoa.", "Ilman sokeria.",
    "Ilman maitoa ja sorbitolia.", "Sokeria ei saa olla.", "Täysin sokeriton.", "Lisäaineeton.",
    "Ei saa sisältää.", "No milk or sugar.", "No nuts, peanuts or sorbitol.", "Sugar-free.",
    "Täysin maidoton ja sokeriton.",
])
@pytest.mark.parametrize("use_llm", [False, True])
def test_unmappable_exclusion_asks_exactly_one_question_even_with_llm(exclusion, use_llm):
    def llm(_text, _today):
        return {"requestedDate": "2026-11-16", "assumptions": [], "items": [
            {"what": "pasta", "quantity": 60, "unit": "kg", "requirements": []},
            {"what": "oat drink", "quantity": 30, "unit": "l", "requirements": []},
        ]}

    result = parse_request(BASE_FI + exclusion, NOW, interpreter=llm if use_llm else None)
    assert result.reasons == [ReasonCode.CLARIFICATION_NEEDED]
    assert result.clarificationQuestion.count("?") == 1


@pytest.mark.parametrize("count", [1, 2, 3])
def test_llm_cannot_omit_global_constraints_even_when_item_count_changes(count):
    def llm(_text, _today):
        return {"requestedDate": "2026-11-16", "assumptions": [], "items": [
            {"what": "pasta", "quantity": 10, "unit": "kg", "requirements": []} for _ in range(count)
        ]}

    result = parse_request(BASE_FI + "Ei saa sisältää pähkinöitä. Koulun keliaakikoille.", NOW, interpreter=llm)
    assert result.mode == "LLM"
    assert result.clarificationQuestion is None
    assert len(result.request.items) == count
    assert all({"AN", "AP", "FREE_FROM_GLUTEN"} <= {r.code for r in item.requirements}
               for item in result.request.items)
    assert any(note.startswith("Applied to every item:") for note in result.request.assumptions)


def test_diet_words_survive_when_only_the_llm_can_parse_the_items():
    def llm(_text, _today):
        return {"requestedDate": "2026-11-16", "assumptions": [], "items": [
            {"what": "pasta", "quantity": 10, "unit": "kg", "requirements": []},
        ]}

    result = parse_request("Our usual pasta for coeliacs by Monday", NOW, interpreter=llm)
    assert result.mode == "LLM"
    assert result.clarificationQuestion is None
    assert result.request.items[0].requirements == [Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")]
    assert "Applied to every item: gluten-free" in result.request.assumptions


def test_item_local_exclusions_do_not_leak_to_other_items_or_brands():
    result = parse_request("20 kg maidotonta juustoa ja 10 litraa maitoa maanantaiksi", NOW)
    assert result.clarificationQuestion is None
    assert [r.code for r in result.request.items[0].requirements] == ["AM"]
    assert result.request.items[1].requirements == []
    branded = parse_request("20 kg Crouton pastaa ja 10 kg suolatonta voita maanantaiksi", NOW)
    assert branded.clarificationQuestion is None
