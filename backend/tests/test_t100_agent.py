"""T100 (judge run 7): a plant base is a hard milk-free requirement, and an exact-type top-up from a second supplier
is not a substitute. Synthetic hand-made records only (patch P7)."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from test_plan_variants import FRESH, FROZEN
from test_split import _named_product

from app.agent.parse import parse_request
from app.contracts import RequestItem
from app.policy.lines import is_exact_top_up
from tests.synthetic.policy import line

NOW = datetime(2026, 11, 14, 22, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


def _requirements(text: str) -> list[str]:
    (item,) = parse_request(text, NOW).request.items
    return [r.code for r in item.requirements]


def test_a_plant_base_excludes_dairy():
    assert _requirements("20 litres of oat-based whipping cream by Monday") == ["AM"]
    assert _requirements("10 l plant-based cream by Monday") == ["AM"]
    assert _requirements("10 l kaurapohjaista kermaa maanantaiksi") == ["AM"]
    (item,) = parse_request("20 litres of oat-based whipping cream by Monday", NOW).request.items
    assert "oat" in item.what  # still an oat product type; milk-free is added, not swapped in


def test_a_plain_oat_product_gets_no_extra_requirement():
    assert _requirements("10 l oat drink by Monday") == []
    assert _requirements("25 kg oat flakes by Monday") == []


EMMENTAL_A = _named_product("SYN-EMM-A", "Synthetic Emmental grated 2 kg", content=("2", "kg"), claims=(),
                            category="10611")
EMMENTAL_B = _named_product("SYN-EMM-B", "Synthetic Valio Emmental red label 2,5 kg grated", content=("2.5", "kg"),
                            claims=(), category="10611")
EDAM_MIX = _named_product("SYN-EMM-E", "Synthetic grated Emmental-Edam cheese 2 kg", content=("2", "kg"),
                          claims=(), category="10611")
SALMON, SALMON_FROZEN = FRESH, FROZEN  # T77's synthetic fresh and frozen salmon fillets


def _line(prod, total: str, kg: str = "100", line_id: str = "L1"):
    return line(line_id=line_id, prod=prod, qty="1", delivered=kg, unit="kg", total=total)


def _item(what: str, **kw) -> RequestItem:
    return RequestItem(itemId="I1", what=what, quantity=Decimal(300), unit="kg", **kw)


def test_the_same_type_form_and_variety_at_a_guarded_price_is_no_substitute():
    primary = _line(EMMENTAL_A, "932.00")
    assert is_exact_top_up(_line(EMMENTAL_B, "940.00", line_id="L2"), primary, _item("grated emmental"))


def test_a_different_variety_variant_price_or_a_named_product_still_asks():
    primary = _line(EMMENTAL_A, "932.00")
    assert not is_exact_top_up(_line(EDAM_MIX, "940.00", line_id="L2"), primary, _item("grated emmental"))
    assert not is_exact_top_up(_line(EMMENTAL_B, "1100.00", line_id="L2"), primary, _item("grated emmental"))
    assert not is_exact_top_up(_line(EMMENTAL_B, "940.00", line_id="L2"), primary,
                               _item("grated emmental", productId="SYN-EMM-A"))
    fresh = _line(SALMON, "1400.00")
    assert not is_exact_top_up(_line(SALMON_FROZEN, "1400.00", line_id="L2"), fresh, _item("salmon fillet"))
    assert is_exact_top_up(_line(SALMON, "1400.00", line_id="L2"), fresh, _item("salmon fillet"))
