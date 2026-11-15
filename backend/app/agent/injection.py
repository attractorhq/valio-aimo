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

_ROLE_EN = (
    r"(?:(?:purchasing|procurement|general|kitchen|hotel|finance|line|duty)\s+)?"
    r"(?:manager|head\s+chef|executive\s+chef|chef|boss|director|approver|ceo|cfo|coo|"
    r"owner|admin|administrator|management|board|valio|human)"
)
_LIMIT_TARGET_EN = (
    r"(?:\s+(?:to|by|up\s+to|at|above|over)\s+[€$£]?\s*\d[\d\s,.]*\s*(?:€|eur|euroa|euros|k)?\b)?"
    r"(?:\s+this\s+time)?"
)
_LIMIT_TARGET_FI = (
    r"(?:\s+[€$£]?\s*\d[\d\s,.]*\s*(?:€|eur|euroon|eurolla)?\b)?"
    r"(?:\s+tämän\s+kerran)?"
)

# English and Finnish. Patterns are matched on lower-cased text; keep them specific enough that
# ordinary purchasing text ("approved supplier", "limit the fat to 1.5 %") does not trigger them.
_PATTERNS: tuple[tuple[Category, str], ...] = (
    ("APPROVAL_CLAIM", r"\b(?:(?:this(?:\s+order|\s+request)?|the\s+order|it)\s+(?:is|was|has\s+been)\s+)?"
                       r"(?:(?:already|pre|previously|has\s+been|have\s+been|was|is|been)[\s-]+)?"
                       r"(?:approved|approval|sign[\s-]?off|ok|okayed|ok'd)\s+(?:by|from)\s+(?:the\s+|our\s+)?"
                       + _ROLE_EN + r"\b(?:\s+as\s+usual)?"),
    ("APPROVAL_CLAIM", r"\b(?:(?:the|our)\s+)?" + _ROLE_EN + r"\s+(?:has\s+|have\s+)?(?:already\s+|previously\s+)?"
                       r"(?:approved|signed\s+off(?:\s+on)?|okayed|ok'd)"
                       r"(?:\s+(?:this(?:\s+order|\s+request)?|it|the\s+order|the\s+request))?\b"),
    ("APPROVAL_CLAIM", r"\b(?:(?:this(?:\s+order|\s+request)?|the\s+order|it)\s+(?:is|was|has\s+been)\s+)?"
                       r"(?:already|pre|previously|has\s+been|have\s+been|was|is|been)[\s-]+approved"
                       r"(?:\s+(?:this(?:\s+order|\s+request)?|it|the\s+order))?\b"),
    ("APPROVAL_CLAIM", r"\b(?:approval|sign[\s-]?off)\s+(?:is\s+|was\s+)?(?:already\s+)?"
                       r"(?:given|granted|done|in\s+place|not\s+needed|not\s+required)\b"),
    ("APPROVAL_CLAIM", r"\b(?:(?:tilaus|tämä)\s+)?(?:on\s+)?(?:jo\s+)?hyväksytty\b|\besihyväksy\w*|"
                       r"\bhyväksynnän\s+(?:jo\s+)?saanut\b"),
    ("LIMIT_CHANGE", r"\b(?:raise|increase|lift|remove|ignore|bypass|override|double|extend|set|change)\s+"
                     r"(?:the\s+|our\s+|my\s+|your\s+|any\s+|all\s+)?"
                     r"(?:auto[\s-]?approv\w*\s+|hard\s+|spending\s+|order\s+|budget\s+|daily\s+|mandate\s+)?"
                     r"(?:limits?|caps?|budgets?|thresholds?)\b" + _LIMIT_TARGET_EN),
    ("LIMIT_CHANGE", r"\bno\s+(?:spending\s+|order\s+)?limits?\b|\bwithout\s+(?:a\s+|any\s+)?limits?\b"),
    ("LIMIT_CHANGE", r"\b(?:nosta|poista|ohita|kasvata|suurenna)\w*\s+(?:\w+\s+)?(?:raja\w*|limiit\w*|katto\w*)"
                     + _LIMIT_TARGET_FI),
    ("RULE_CHANGE", r"\b(?:change|update|modify|disable|turn\s+off|relax|ignore|bypass|skip)\s+"
                    r"(?:the\s+|our\s+|your\s+|all\s+)?"
                    r"(?:rules?|mandates?|polic(?:y|ies)|allergen\s+checks?|checks?|verification)\b"
                    r"(?:\s+this\s+time)?"),
    ("RULE_CHANGE", r"\b(?:treat|mark|consider)\s+(?:it|this|them|everything|all)\s+(?:as\s+)?"
                    r"(?:verified|approved|safe)\b"),
    ("RULE_CHANGE", r"\b(?:ohita|muuta|poista)\s+(?:\w+\s+)?(?:sään\w*|säänn\w*|tarkist\w*|mandaat\w*)"),
    ("SYSTEM_INSTRUCTION", r"\b(?:ignore|disregard|forget)\s+"
                           r"(?:all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+)*"
                           r"(?:instructions?|prompts?|rules?|guidelines?)\b"),
    ("SYSTEM_INSTRUCTION", r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|system\s+prompt|developer\s+mode|jailbreak)\b"),
    ("SYSTEM_INSTRUCTION", r"\bauto[\s-]?approve(?:\s+(?:it|this(?:\s+order)?|the\s+order))?\b|"
                           r"\bapprove\s+(?:it|this(?:\s+order)?|the\s+order|automatically|immediately|now)\b"),
    ("SYSTEM_INSTRUCTION", r"\b(?:skip|bypass|no\s+need\s+for|without)\s+(?:the\s+|any\s+|human\s+)?"
                           r"(?:approval|approver|sign[\s-]?off|human(?:\s+review|\s+approval)?)\b"),
    ("SYSTEM_INSTRUCTION", r"\b(?:do\s+not|don't|dont|no\s+need\s+to|there\s+is\s+no\s+need\s+to|never)\s+"
                           r"(?:ask|check\s+with|double[\s-]?check\s+with|confirm\s+with|verify\s+with|"
                           r"consult(?:\s+with)?|clear\s+(?:it|this)\s+with|bother|contact|escalate\s+to)"
                           r"(?:\s+(?:anyone|anybody|someone|somebody|a\s+human|a\s+person|"
                           r"for\s+approval|for\s+sign[\s-]?off|for\s+permission|"
                           r"(?:the\s+|our\s+)?" + _ROLE_EN + r"(?:\s+for\s+approval)?))\b|"
                           r"\b(?:do\s+not|don't|dont|no\s+need\s+to|never)\s+ask\b"),
    ("SYSTEM_INSTRUCTION", r"\bwithout\s+(?:checking|asking|confirming|consulting|verifying)(?:\s+with)?\s+"
                           r"(?:anyone|anybody|someone|somebody|a\s+human|"
                           r"(?:the\s+|our\s+)?" + _ROLE_EN + r")\b"),
    ("SYSTEM_INSTRUCTION", r"\bhyväksy\s+(?:tämä|tilaus|automaattisesti|heti|suoraan)\b|"
                           r"\b(?:älä|ei\s+tarvitse|ei\s+tarvi)\s+(?:kysyä?|tarkistaa?|varmistaa?)"
                           r"(?:\s+(?:keneltäkään|hyväksyntää|lupaa|\w*päällik\w*|\w*johtaj\w*|esimie\w*|keittiömestar\w*))?\b|"
                           r"\bilman\s+(?:hyväksyntää|lupaa|tarkistusta)\b|"
                           r"\bohita\s+hyväksyntä\w*|\bunohda\s+(?:\w+\s+)?ohjee\w*"),
    ("SYSTEM_INSTRUCTION", r"\b(?:fråga\s+inte|behöver\s+inte\s+(?:fråga|kolla\s+med))"
                           r"(?:\s+(?:någon|inköpschef\w*|köksmästar\w*|chef\w*))?\b|"
                           r"\butan\s+godkännande\b|\bignorera\s+(?:\w+\s+)?(?:gräns\w*|regl\w*|instruktion\w*)\b"),
)
_COMPILED = tuple((category, re.compile(pattern, re.IGNORECASE)) for category, pattern in _PATTERNS)


@dataclass(frozen=True)
class IgnoredInstruction:
    category: Category
    text: str
    start: int
    end: int


def contains_instruction(text: str) -> bool:
    """True when any pattern matches. Used by the gateway's O4 rule; does not log (the parser logs the spans)."""
    return any(pattern.search(text) for _, pattern in _COMPILED)


def detect_instructions(text: str) -> list[IgnoredInstruction]:
    """Return every instruction-like span in the request text, in text order, without overlaps."""
    found: list[IgnoredInstruction] = []
    for category, pattern in _COMPILED:
        for match in pattern.finditer(text):
            cand = IgnoredInstruction(category, match.group(0), match.start(), match.end())
            overlapping = [f for f in found if cand.start < f.end and f.start < cand.end]
            if not overlapping:
                found.append(cand)
            elif all(cand.start <= f.start and cand.end >= f.end for f in overlapping) and (
                (cand.end - cand.start) > max(f.end - f.start for f in overlapping)
            ):
                found = [f for f in found if f not in overlapping]
                found.append(cand)
    found.sort(key=lambda f: f.start)
    for finding in found:
        # Always logged (O4). The phrase is untrusted request data, logged as a repr, never executed.
        logger.warning("INSTRUCTION_IGNORED category=%s phrase=%r", finding.category, finding.text)
    return found
