"""T100: whole-pack rounding far above the amount asked (25 kg of oat flakes as 2 × 20 kg = 40 kg) is a quantity
trade-off a human signs off, never AUTO. Synthetic records only (patch P7)."""

from decimal import Decimal

from app.contracts import ReasonCode
from app.policy import M1, decide
from app.policy.decision import overbuy_detail
from tests.synthetic.policy import NOW, line, product, request
from tests.synthetic.policy import item as policy_item


def _decide(asked: str, delivered: str, units: str = "2"):
    return decide(
        mandate=M1,
        request=request(policy_item(quantity=asked, unit="kg")),
        lines=[line(prod=product(), qty=units, delivered=delivered, unit="kg", total="40.00")],
        now=NOW,
    )


def test_forty_kilos_for_twenty_five_asked_is_asked_about():
    decision = _decide("25", "40")
    assert decision.outcome == "ASK"
    (finding,) = [f for f in decision.findings if f.code == ReasonCode.QUANTITY_BASIS_WEAK]
    assert finding.detail == "40 kg for 25 kg asked: whole 20 kg packs"


def test_rounding_up_to_a_quarter_more_stays_auto():
    assert _decide("32", "40").outcome == "AUTO"  # +25 %
    assert _decide("40", "40").outcome == "AUTO"


def test_the_detail_names_mixed_packs_without_a_size():
    lines = [line(prod=product(), qty="1", delivered="20", unit="kg", total="1"),
             line(line_id="L2", prod=product(), qty="2", delivered="10", unit="kg", total="1")]
    assert overbuy_detail(Decimal(30), Decimal(12), "kg", lines) == "30 kg for 12 kg asked: whole packs"
