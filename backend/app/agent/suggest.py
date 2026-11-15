"""T97: a vague request ("something for the weekend brunch", "some cheese, the usual amount") gets 2–3 complete
requests the buyer can run with one tap, instead of only a generic "what, how much, by when?".

The options are request texts, not orders and not product facts: each one goes through the normal flow (parse,
lookup, live price and stock, policy) when the buyer picks it. Every option is checked against the deterministic
parser first, so a chip never leads to another clarification.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from app.agent.parse import _OCCASION_EN_RE, _OCCASION_FI_RE, _find_dates, _is_finnish, parse_request
from app.agent.plan import requirement_display_name
from app.catalog.vocab import extract_inline_requirements
from app.contracts.common import Contract


class ClarificationOption(Contract):
    label: str  # "10 kg sliced cheese"
    requestText: str  # "10 kg sliced cheese for the weekend brunch"


@dataclass(frozen=True)
class _Kind:
    en: str
    fi: str  # partitive, as a buyer writes it: "10 kg juustoviipaleita"
    unit: str
    amounts: tuple[int, int, int]
    words: tuple[str, ...]  # regex stems that name this kind in a vague request


# Common food-service dairy and kitchen staples. Order matters: a more specific kind comes first.
_KINDS: tuple[_Kind, ...] = (
    _Kind("sliced cheese", "juustoviipaleita", "kg", (5, 10, 20),
          (r"sliced\b.*\bcheese", r"cheese\b.*\bsliced", "cheese slice", "juustoviipal", r"viipaloi\w*\s+juust")),
    _Kind("grated cheese", "raastettua juustoa", "kg", (5, 10, 20),
          (r"grated\b.*\bcheese", r"cheese\b.*\bgrated", "juustoraaste", r"raastet\w*\s+juust")),
    _Kind("cheese", "juustoa", "kg", (5, 10, 20), ("cheese", "juusto")),
    _Kind("coffee cream", "kahvikermaa", "l", (10, 20, 40), ("coffee cream", "kahvikerma")),
    _Kind("whipping cream", "vispikermaa", "l", (10, 20, 40), ("whipping cream", "vispikerma", "kuohukerma")),
    _Kind("cooking cream", "ruokakermaa", "l", (10, 20, 40), ("cooking cream", "ruokakerma")),
    _Kind("cream", "kermaa", "l", (10, 20, 40), ("cream", "kerma")),
    _Kind("oat drink", "kaurajuomaa", "l", (10, 20, 50), ("oat drink", "oat milk", "kaurajuoma")),
    _Kind("milk", "maitoa", "l", (20, 50, 100), ("milk", "maito")),
    _Kind("yoghurt", "jogurttia", "kg", (5, 10, 20), ("yoghurt", "yogurt", "jogurt")),
    _Kind("quark", "rahkaa", "kg", (5, 10, 20), ("quark", "rahka")),
    _Kind("butter", "voita", "kg", (5, 10, 20), ("butter", "voi")),
)
_BY_EN = {k.en: k for k in _KINDS}

# What a kitchen usually buys for an occasion, most likely first. Product types only; nothing about stock or price.
_OCCASIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("brunch", "breakfast", "aamiai", "brunssi"), ("sliced cheese", "yoghurt", "butter")),
    (("coffee", "kahvi"), ("coffee cream", "milk", "oat drink")),
    (("baking", "leivont", "dessert", "jälkiruo", "jälkiruu"), ("butter", "whipping cream", "quark")),
    (("lunch", "dinner", "lounas", "lounaa", "päivälli", "illalli"), ("cooking cream", "grated cheese", "milk")),
    (("buffet", "party", "event", "juhl", "tapahtum", "buffeti"), ("sliced cheese", "cheese", "yoghurt")),
)
_DEFAULT = ("milk", "butter", "sliced cheese")
_WEEKEND_RE = re.compile(r"\b(?:the\s+)?weekend\b|\bviikonlopu\w*", re.IGNORECASE)


_SUBTYPE_PARENTS: dict[str, str] = {
    "sliced cheese": "cheese",
    "grated cheese": "cheese",
    "coffee cream": "cream",
    "whipping cream": "cream",
    "cooking cream": "cream",
    "oat drink": "milk",
}


def _kinds_named(text: str) -> list[_Kind]:
    """All distinct product kinds named in `text`, in text order (specific subtypes win over their parent)."""
    cleaned, _ = extract_inline_requirements(text)
    low = cleaned.lower()
    matched: list[tuple[int, _Kind]] = []
    covered_parents: set[str] = set()
    for kind in _KINDS:
        if kind.en in covered_parents:
            continue
        positions = [m.start() for word in kind.words if (m := re.search(rf"\b{word}", low))]
        if positions:
            matched.append((min(positions), kind))
            if parent := _SUBTYPE_PARENTS.get(kind.en):
                covered_parents.add(parent)
    matched.sort(key=lambda entry: entry[0])
    return [kind for _, kind in matched]


def _kind_named(text: str) -> _Kind | None:
    kinds = _kinds_named(text)
    return kinds[0] if kinds else None


def _occasion_kinds(text: str) -> tuple[str, ...]:
    low = text.lower()
    for words, kinds in _OCCASIONS:
        if any(word in low for word in words):
            return kinds
    return _DEFAULT


def _context(text: str, now: datetime, finnish: bool) -> str:
    """The parts of the request that say when and what for: dates, 'weekend', the occasion. In text order."""
    spans = []
    for d in _find_dates(text, now.date()):
        lead = re.search(r"\b(?:by|on|before|until|for)\s+$", text[:d.start], re.IGNORECASE)
        spans.append((lead.start() if lead else d.start, d.end))
    spans += [(m.start(), m.end()) for m in _WEEKEND_RE.finditer(text)]
    occasion = _OCCASION_FI_RE if finnish else _OCCASION_EN_RE
    spans += [(m.start(), m.end()) for m in occasion.finditer(text)]
    # "for the weekend brunch": take the whole "for … <occasion noun>" phrase when the weekend sits inside it.
    if not finnish:
        spans += [(m.start(), m.end()) for m in re.finditer(
            r"\bfor\s+(?:the\s+|our\s+)?weekend\s+\w+", text, re.IGNORECASE)]
    else:
        spans += [(m.start(), m.end()) for m in re.finditer(r"\bviikonlopun\s+\w+", text, re.IGNORECASE)]
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return " ".join(" ".join(text[s:e].split()) for s, e in merged)


_STATED_QTY_RE = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(kg|kilos?|kiloa|kilogram\w*|l|litres?|liters?|litraa)\b", re.IGNORECASE)
_LITRES = ("l", "litre", "litres", "liter", "liters", "litraa")


def _stated_amount(text: str, unit: str) -> int | None:
    """A number the buyer already gave in the same unit ("maybe 20 kilos"): offered first, never replaced."""
    for m in _STATED_QTY_RE.finditer(text):
        is_litre = m.group(2).lower() in _LITRES
        if is_litre == (unit == "l"):
            try:
                return int(float(m.group(1).replace(",", ".")))
            except ValueError:
                return None
    return None


_LEAD_IN_RE = re.compile(
    r"^\s*(?:(?:we|i)\s+(?:need|want|would\s+like)|need|please(?:\s+(?:send|order|get))?|send|order|"
    r"tarvitaan|tarvitsemme|tarvitsen|tilataan|tilaan|haluaisimme|haluamme)\b[\s,:]*(?:some\s+)?",
    re.IGNORECASE,
)
_TRAILING_PLEASE_RE = re.compile(r"[\s,]*\b(?:please|kiitos)\W*$", re.IGNORECASE)


def _without_lead_in(text: str) -> str:
    """'We need lactose-free milk by Monday.' -> 'lactose-free milk by Monday.' so an amount can go in front."""
    return _TRAILING_PLEASE_RE.sub("", _LEAD_IN_RE.sub("", text, count=1)).strip()


def _codes(requirements) -> set[tuple[str, str]]:
    return {(r.kind, r.code) for r in requirements}


def clarification_options(text: str, now: datetime, *, limit: int = 3) -> list[ClarificationOption]:
    """2–3 complete request texts for a request that needed clarification. Empty when none would parse cleanly.

    Every requirement the buyer wrote ("lactose-free", "gluteeniton") stays in each option; an option that loses
    one is dropped, never offered.
    """
    finnish = _is_finnish(text)
    context = _context(text, now, finnish)
    _, stated_requirements = extract_inline_requirements(text)
    wanted = _codes(stated_requirements)
    requirement_words = " ".join(requirement_display_name(r) for r in stated_requirements)
    named_kinds = _kinds_named(text)
    if len(named_kinds) >= 2:
        joiner = " ja " if finnish else " and "
        options: list[ClarificationOption] = []
        for tier_idx in range(3):
            parts: list[str] = []
            for kind in named_kinds:
                amount = kind.amounts[min(tier_idx, len(kind.amounts) - 1)]
                noun = kind.fi if finnish else kind.en
                parts.append(" ".join(p for p in (f"{amount} {kind.unit}", requirement_words, noun) if p))
            chip = joiner.join(parts)
            candidate = f"{chip} {context}".strip()
            parsed = parse_request(candidate, now)
            if (
                parsed.request is None
                or parsed.clarificationQuestion is not None
                or len(parsed.request.items) != len(named_kinds)
            ):
                continue
            if not all(wanted <= _codes(item.requirements) for item in parsed.request.items):
                continue
            options.append(ClarificationOption(label=chip, requestText=candidate))
            if len(options) == limit:
                break
        return options

    named = named_kinds[0] if named_kinds else None
    if named is not None:
        stated = _stated_amount(text, named.unit)
        amounts = [stated] if stated else []
        amounts += [a for a in named.amounts if a not in amounts]
        drafts = [(named, amount) for amount in amounts]
    else:
        drafts = [(_BY_EN[en], _BY_EN[en].amounts[1]) for en in _occasion_kinds(text)]

    options = []
    for kind, amount in drafts:
        noun = kind.fi if finnish else kind.en
        label = " ".join(part for part in (f"{amount} {kind.unit}", requirement_words, noun) if part)
        # The buyer's own words first (keeps their language and requirement wording), then a clean rewrite.
        own_text = f"{amount} {kind.unit} {_without_lead_in(text)}"
        own = [(own_text, own_text)] if named is not None else []
        for candidate, chip in [*own, (f"{label} {context}".strip(), label)]:
            parsed = parse_request(candidate, now)  # deterministic: no interpreter
            if parsed.request is None or parsed.clarificationQuestion is not None or len(parsed.request.items) != 1:
                continue
            if not wanted <= _codes(parsed.request.items[0].requirements):
                continue
            options.append(ClarificationOption(label=chip, requestText=candidate))
            break
        if len(options) == limit:
            break
    return options
