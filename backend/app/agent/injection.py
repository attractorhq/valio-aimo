"""O4: deterministic detector for instructions hidden in request text (brief §6.5 O4, §7.11).

Request text is data. Anything that tries to change rules, raise limits, claim a prior approval or
instruct the system is ignored and always logged as INSTRUCTION_IGNORED. Approval only ever comes
through the approval channel, so nothing found here can change a decision in the buyer's favour.
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("app.agent.injection")

Category = Literal["APPROVAL_CLAIM", "LIMIT_CHANGE", "RULE_CHANGE", "SYSTEM_INSTRUCTION"]

# English and Finnish. Patterns are matched on lower-cased text; keep them specific enough that
# ordinary purchasing text ("approved supplier", "limit the fat to 1.5 %") does not trigger them.
_PATTERNS: tuple[tuple[Category, str], ...] = (
    ("APPROVAL_CLAIM", r"\b(?:already|pre|previously|has been|have been|was|is|been)[\s-]+approved\b"),
    ("APPROVAL_CLAIM", r"\b(?:approved|approval|sign[\s-]?off|ok)\s+(?:by|from)\s+(?:the\s+|our\s+)?"
                       r"(?:manager|head chef|chef|boss|director|approver|ceo|cfo|owner|admin|management|valio)\b"
                       r"(?:\s+as\s+usual)?"),
    ("APPROVAL_CLAIM", r"\b(?:manager|head chef|boss|director|approver|management)\s+(?:has\s+)?(?:already\s+)?"
                       r"(?:approved|signed off|okayed|ok'd)\b"),
    ("APPROVAL_CLAIM", r"\b(?:approval|sign[\s-]?off)\s+(?:is\s+|was\s+)?(?:already\s+)?"
                       r"(?:given|granted|done|in place|not needed|not required)\b"),
    ("APPROVAL_CLAIM", r"\b(?:on\s+)?(?:jo\s+)?hyväksytty\b|\besihyväksy\w*|\bhyväksynnän\s+(?:jo\s+)?saanut\b"),
    ("LIMIT_CHANGE", r"\b(?:raise|increase|lift|remove|ignore|bypass|override|double|extend)\s+(?:the\s+|our\s+|my\s+|your\s+|any\s+|all\s+)?"
                     r"(?:auto[\s-]?approv\w*\s+|hard\s+|spending\s+|order\s+|budget\s+)?(?:limits?|caps?|budgets?|thresholds?)\b"),
    ("LIMIT_CHANGE", r"\bno\s+(?:spending\s+|order\s+)?limits?\b|\bwithout\s+(?:a\s+|any\s+)?limits?\b"),
    ("LIMIT_CHANGE", r"\b(?:nosta|poista|ohita|kasvata|suurenna)\w*\s+(?:\w+\s+)?(?:raja\w*|limiit\w*|katto\w*)"),
    ("RULE_CHANGE", r"\b(?:change|update|modify|disable|turn off|relax|ignore|bypass|skip)\s+(?:the\s+|our\s+|your\s+|all\s+)?"
                    r"(?:rules?|mandates?|polic(?:y|ies)|allergen checks?|checks?|verification)\b"),
    ("RULE_CHANGE", r"\b(?:treat|mark|consider)\s+(?:it|this|them|everything|all)\s+(?:as\s+)?(?:verified|approved|safe)\b"),
    ("RULE_CHANGE", r"\b(?:ohita|muuta|poista)\s+(?:\w+\s+)?(?:sään\w*|säänn\w*|tarkist\w*|mandaat\w*)"),
    ("SYSTEM_INSTRUCTION", r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+)*"
                           r"(?:instructions?|prompts?|rules?|guidelines?)\b"),
    ("SYSTEM_INSTRUCTION", r"\b(?:you are now|act as|pretend to be|system prompt|developer mode|jailbreak)\b"),
    ("SYSTEM_INSTRUCTION", r"\bauto[\s-]?approve\b|\bapprove\s+(?:it|this|the order|automatically|immediately|now)\b"),
    ("SYSTEM_INSTRUCTION", r"\b(?:skip|bypass|no need for|without)\s+(?:the\s+|any\s+)?(?:approval|approver|sign[\s-]?off|human)\b"),
    ("SYSTEM_INSTRUCTION", r"\b(?:do not|don't|dont|no need to)\s+ask\b"),
    ("SYSTEM_INSTRUCTION", r"\bhyväksy\s+(?:tämä|tilaus|automaattisesti|heti|suoraan)\b|\bälä\s+kysy\b|"
                           r"\bohita\s+hyväksyntä\w*|\bunohda\s+(?:\w+\s+)?ohjee\w*"),
)
_COMPILED = tuple((category, re.compile(pattern, re.IGNORECASE)) for category, pattern in _PATTERNS)


@dataclass(frozen=True)
class IgnoredInstruction:
    category: Category
    text: str
    start: int
    end: int


def detect_instructions(text: str) -> list[IgnoredInstruction]:
    """Return every instruction-like span in the request text, in text order, without overlaps."""
    found: list[IgnoredInstruction] = []
    for category, pattern in _COMPILED:
        for match in pattern.finditer(text):
            if any(match.start() < f.end and f.start < match.end() for f in found):
                continue
            found.append(IgnoredInstruction(category, match.group(0), match.start(), match.end()))
    found.sort(key=lambda f: f.start)
    for finding in found:
        # Always logged (O4). The phrase is untrusted request data, logged as a repr, never executed.
        logger.warning("INSTRUCTION_IGNORED category=%s phrase=%r", finding.category, finding.text)
    return found
