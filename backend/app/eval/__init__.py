"""Evaluation suite (24 rows) runner (T16)."""

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
    "PriceOverlayProvider",
    "ReferenceFixtureProvider",
    "build_reference_products",
    "evaluate_row",
    "execute_row_proposal",
    "find_latest_fixture_capture",
    "format_eval_table",
    "run_eval_suite",
]
