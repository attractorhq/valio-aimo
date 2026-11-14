"""Live pre-flight check (Patch P5.2): runs A, B, C and B2 against the live API and compares against expected."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.contracts import Product, ReasonCode
from app.eval.rows import SCENARIO_ROWS, EvalRow
from app.eval.runner import (
    DEFAULT_FIXTURES_DIR,
    FROZEN_NOW,
    ReferenceFixtureProvider,
    build_reference_products,
    execute_row_proposal,
    find_latest_fixture_capture,
)
from app.gateway.offers import Lookup
from app.gateway.proposals import NoOrderError
from app.valio import IntegrationError, LiveValioProvider, RecordedValioProvider


@dataclass(frozen=True)
class PreflightScenarioResult:
    scenario_id: str
    mandate_id: str
    expected_decision: str
    live_decision: str | None
    expected_reasons: tuple[str, ...]
    live_reasons: tuple[str, ...]
    expected_total_excl_vat: str
    live_total_excl_vat: str | None
    green: bool
    differences: dict[str, str]
    summary: str


@dataclass(frozen=True)
class PreflightReport:
    checked_at: str
    green: bool
    recommended_mode: Literal["LIVE", "RECORDED"]
    recorded_at: str | None
    duration_seconds: float
    scenarios: tuple[PreflightScenarioResult, ...]
    banner: str


def _compare_scenario(
    row: EvalRow,
    *,
    products: dict[str, Product],
    baseline_provider: Lookup,
    live_provider: Lookup,
) -> PreflightScenarioResult:
    _, expected_prop = execute_row_proposal(row, products=products, provider=baseline_provider)
    exp_decision = row.expected_decision or expected_prop.decision
    exp_reasons = tuple(str(r) for r in (row.expected_reasons or expected_prop.reasons))
    exp_total = format(row.expected_total_excl_vat or expected_prop.totals.exclVat, ".2f")

    diffs: dict[str, str] = {}
    try:
        _, live_prop = execute_row_proposal(row, products=products, provider=live_provider)
    except NoOrderError as exc:
        live_codes = tuple(str(c) for c in exc.policy_decision.reasons)
        excluded_codes = {
            f.code for v in exc.policy_decision.excluded for f in v.findings
        }
        if ReasonCode.CUTOFF_PASSED in excluded_codes or "CUTOFF_PASSED" in excluded_codes:
            diffs["cutoff"] = "cut-off passed on live API (CUTOFF_PASSED -> cannot be promised)"
        if ReasonCode.NOT_AVAILABLE in excluded_codes or "NOT_AVAILABLE" in excluded_codes:
            diffs["stock"] = "out of stock on live API (NOT_AVAILABLE)"
        if ReasonCode.PRICE_UNAVAILABLE in excluded_codes or "PRICE_UNAVAILABLE" in excluded_codes:
            diffs["price"] = "price unavailable or outside validity window"
        if not diffs:
            diffs["reasons"] = f"NO_ORDER ({', '.join(live_codes)})"
        return PreflightScenarioResult(
            scenario_id=row.scenario_id or f"R{row.row_id}",
            mandate_id=row.mandate_id,
            expected_decision=exp_decision,
            live_decision="NO_ORDER",
            expected_reasons=exp_reasons,
            live_reasons=live_codes,
            expected_total_excl_vat=exp_total,
            live_total_excl_vat=None,
            green=False,
            differences=diffs,
            summary="; ".join(f"{k}: {v}" for k, v in diffs.items()),
        )
    except (IntegrationError, Exception) as exc:
        msg = str(exc) or exc.__class__.__name__
        diffs["api"] = f"live API unreachable or unconfigured ({msg})"
        return PreflightScenarioResult(
            scenario_id=row.scenario_id or f"R{row.row_id}",
            mandate_id=row.mandate_id,
            expected_decision=exp_decision,
            live_decision=None,
            expected_reasons=exp_reasons,
            live_reasons=(),
            expected_total_excl_vat=exp_total,
            live_total_excl_vat=None,
            green=False,
            differences=diffs,
            summary=diffs["api"],
        )

    live_decision = live_prop.decision
    live_reasons = tuple(str(r) for r in live_prop.reasons)
    live_total = format(live_prop.totals.exclVat, ".2f")

    exp_lines_by_pid = {ln.productId: ln for ln in expected_prop.lines}
    live_lines_by_pid = {ln.productId: ln for ln in live_prop.lines}

    # Price diff check
    price_diffs: list[str] = []
    if live_total != exp_total:
        price_diffs.append(f"total €{exp_total} -> €{live_total}")
    for pid, exp_ln in exp_lines_by_pid.items():
        live_ln = live_lines_by_pid.get(pid)
        if live_ln and live_ln.unitPriceExclVat != exp_ln.unitPriceExclVat:
            price_diffs.append(f"{pid} €{exp_ln.unitPriceExclVat} -> €{live_ln.unitPriceExclVat}")
    if price_diffs:
        diffs["price"] = ", ".join(price_diffs)

    # Stock / allocation diff check
    stock_diffs: list[str] = []
    if set(exp_lines_by_pid) != set(live_lines_by_pid):
        stock_diffs.append(f"lines {sorted(exp_lines_by_pid)} -> {sorted(live_lines_by_pid)}")
    else:
        for pid, exp_ln in exp_lines_by_pid.items():
            live_ln = live_lines_by_pid[pid]
            if live_ln.quantitySalesUnits != exp_ln.quantitySalesUnits:
                stock_diffs.append(f"{pid} qty {exp_ln.quantitySalesUnits} -> {live_ln.quantitySalesUnits}")
    if stock_diffs:
        diffs["stock"] = ", ".join(stock_diffs)

    # Delivery date diff check
    date_diffs: list[str] = []
    for pid, exp_ln in exp_lines_by_pid.items():
        live_ln = live_lines_by_pid.get(pid)
        if live_ln and live_ln.deliveryDate != exp_ln.deliveryDate:
            date_diffs.append(f"{pid} {exp_ln.deliveryDate} -> {live_ln.deliveryDate}")
    if date_diffs:
        diffs["date"] = ", ".join(date_diffs)

    # Cut-off diff check
    cutoff_ex = [
        e.productId
        for e in live_prop.excluded
        if ReasonCode.CUTOFF_PASSED in e.reasons
        and e.productId in exp_lines_by_pid
    ]
    if cutoff_ex:
        diffs["cutoff"] = f"cut-off passed for {', '.join(cutoff_ex)}"

    if live_decision != exp_decision or live_reasons != exp_reasons:
        diffs["decision"] = f"{exp_decision} {list(exp_reasons)} -> {live_decision} {list(live_reasons)}"

    green = len(diffs) == 0
    return PreflightScenarioResult(
        scenario_id=row.scenario_id or f"R{row.row_id}",
        mandate_id=row.mandate_id,
        expected_decision=exp_decision,
        live_decision=live_decision,
        expected_reasons=exp_reasons,
        live_reasons=live_reasons,
        expected_total_excl_vat=exp_total,
        live_total_excl_vat=live_total,
        green=green,
        differences=diffs,
        summary="no differences (price, stock, date, cut-off match)" if green else "; ".join(f"{k}: {v}" for k, v in diffs.items()),
    )


def run_preflight(
    *,
    live_provider: Lookup | None = None,
    baseline_provider: Lookup | None = None,
    products: dict[str, Product] | None = None,
) -> PreflightReport:
    """Run scenarios A, B, C and B2 against the live API and return a PreflightReport."""
    t0 = time.perf_counter()
    resolved_products = products if products is not None else build_reference_products()

    capture = find_latest_fixture_capture(DEFAULT_FIXTURES_DIR)
    if baseline_provider is not None:
        base = baseline_provider
        recorded_at = getattr(base, "recorded_at", FROZEN_NOW).isoformat()
    elif capture is not None:
        rec = RecordedValioProvider(capture)
        base = rec
        recorded_at = rec.recorded_at.isoformat()
    else:
        ref = ReferenceFixtureProvider()
        base = ref
        recorded_at = ref.recorded_at.isoformat()

    live = live_provider if live_provider is not None else LiveValioProvider()

    scenario_order = ("A", "B", "C", "B2")
    results: list[PreflightScenarioResult] = []
    for sid in scenario_order:
        row = SCENARIO_ROWS[sid]
        results.append(
            _compare_scenario(
                row,
                products=resolved_products,
                baseline_provider=base,
                live_provider=live,
            )
        )

    duration = round(time.perf_counter() - t0, 3)
    # Core P0 demo scenarios are A, B, C (plus B2)
    all_green = all(r.green for r in results)
    mode: Literal["LIVE", "RECORDED"] = "LIVE" if all_green else "RECORDED"
    checked_at = datetime.now(UTC).isoformat()

    if all_green:
        banner = "GREEN — All scenarios (A, B, C, B2) match expected decisions, prices, stock, dates and cut-offs. Present in LIVE mode."
    else:
        banner = (
            f"RED — Live API differs from expected demo baseline. Switch to RECORDED mode "
            f"(captured {recorded_at}) and state this openly."
        )

    return PreflightReport(
        checked_at=checked_at,
        green=all_green,
        recommended_mode=mode,
        recorded_at=recorded_at,
        duration_seconds=duration,
        scenarios=tuple(results),
        banner=banner,
    )


def format_preflight_table(report: PreflightReport) -> str:
    lines = [
        f"Live Pre-flight Check ({report.duration_seconds:.2f}s) · Recommended mode: {report.recommended_mode}",
        report.banner,
        "-" * 110,
        f"{'Scenario':<10} {'Expected':<34} {'Live':<34} {'Differences (price, stock, date, cut-off)'}",
        "-" * 110,
    ]
    for s in report.scenarios:
        exp_str = f"{s.expected_decision} {list(s.expected_reasons)} (€{s.expected_total_excl_vat})"
        if s.live_decision is None:
            live_str = "ERROR (unreachable)"
        elif s.live_total_excl_vat is None:
            live_str = f"{s.live_decision} {list(s.live_reasons)}"
        else:
            live_str = f"{s.live_decision} {list(s.live_reasons)} (€{s.live_total_excl_vat})"
        mark = "GREEN" if s.green else "RED"
        lines.append(f"{s.scenario_id + ' (' + mark + ')':<10} {exp_str:<34} {live_str:<34} {s.summary}")
    lines.append("-" * 110)
    return "\n".join(lines)
