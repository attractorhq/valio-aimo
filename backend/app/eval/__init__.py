"""Evaluation suite (24 rows) and live pre-flight runner (T16)."""

from .preflight import (
    PreflightReport,
    PreflightScenarioResult,
    format_preflight_table,
    run_preflight,
)
from .rows import EVAL_ROWS, SCENARIO_ROWS, EvalRow, ExpectedLineSpec
from .runner import (
    EvalRowResult,
    EvalSuiteReport,
    PriceOverlayProvider,
    ReferenceFixtureProvider,
    build_reference_products,
    evaluate_row,
    execute_row_proposal,
    find_latest_fixture_capture,
    format_eval_table,
    run_eval_suite,
)

__all__ = [
    "EVAL_ROWS",
    "SCENARIO_ROWS",
    "EvalRow",
    "EvalRowResult",
    "EvalSuiteReport",
    "ExpectedLineSpec",
    "PreflightReport",
    "PreflightScenarioResult",
    "PriceOverlayProvider",
    "ReferenceFixtureProvider",
    "build_reference_products",
    "evaluate_row",
    "execute_row_proposal",
    "find_latest_fixture_capture",
    "format_eval_table",
    "format_preflight_table",
    "run_eval_suite",
    "run_preflight",
]
