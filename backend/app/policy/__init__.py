"""Deterministic gateway policy (brief §6, patch P2). Pure: no network, no clock, Decimal money.

The LLM never calls into this to choose an outcome; the gateway does, with facts from the API and dataset.
"""

from .decision import Outcome, PolicyDecision, decide
from .findings import RULE_ORDER, Finding
from .instructions import detect_instruction
from .lines import ContentBasis, LineInput, LineVerdict, StockKind, evaluate_line
from .mandates import DEMO_MANDATES, M1, M2, MandateBook, change_mandate
from .requirements import EU14, X99_PROVES, allergen_status, check_requirements, hard_requirements

__all__ = [
    "DEMO_MANDATES", "EU14", "M1", "M2", "RULE_ORDER", "X99_PROVES", "ContentBasis", "Finding", "LineInput",
    "LineVerdict", "MandateBook", "Outcome", "PolicyDecision", "StockKind", "allergen_status", "change_mandate",
    "check_requirements", "decide", "detect_instruction", "evaluate_line", "hard_requirements",
]
