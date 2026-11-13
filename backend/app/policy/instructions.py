"""Rule O4: request text is data. Spot attempts to change rules or claim approval (EN/FI/SV).

The text is never executed; a match only adds INFO · INSTRUCTION_IGNORED so the attempt is visible and logged.
Deterministic, so it works without the LLM; the parser may flag more cases.
"""

import re

_PATTERNS = (
    # English
    r"\bignore\b.{0,40}\b(rules?|limits?|instructions?|policy|polic(y|ies)|mandate|approval)",
    r"\b(already|pre-?)\s*approved\b",
    r"\bapproved\s+(by|in advance)\b",
    r"\b(raise|increase|lift|remove)\b.{0,30}\blimit",
    r"\b(override|bypass|disable)\b",
    r"\b(skip|without|no)\s+(the\s+|any\s+)?approvals?\b",
    r"\bauto-?approve\b",
    r"\b(system prompt|you are now|new instructions?)\b",
    # Finnish
    r"\bohita\b",
    r"\b(jo|valmiiksi|etukäteen)\s+hyväksyt",
    r"\bhyväksytty\s+jo\b",
    r"\b(nosta|poista)\b.{0,30}\braja",
    r"\bilman\s+hyväksyntää\b",
    # Swedish
    r"\bignorera\b",
    r"\b(redan|förhands)\s*godkän",
    r"\butan\s+godkännande\b",
    r"\b(höj|ta bort)\b.{0,30}\bgräns",
)
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)


def detect_instruction(text: str) -> bool:
    return _REGEX.search(text) is not None
