"""Buyer text (English or Finnish) -> StructuredRequest (brief §7.2), with O4 and O6 (§6.5).

- The LLM (llm.py) interprets the text when configured. Its output is validated against the
  StructuredRequest contract; anything invalid, slow (> deadline) or failing falls back.
- The deterministic fallback (regex + vocabulary) handles scenarios A, B and C on its own.
- O4 runs deterministically on every request, whichever path parses it: instruction-like text is
  ignored and logged as INSTRUCTION_IGNORED.
- O6: when the quantity or item is unclear, the unit is unsupported, or the requirements contradict
  each other, the result carries exactly one clarifying question and CLARIFICATION_NEEDED.

The parser never decides AUTO / ASK / BLOCK and never invents product facts. Pure apart from the
optional LLM call: `now` is passed in.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agent.injection import IgnoredInstruction, detect_instructions
from app.agent.llm import LlmInterpreter, call_with_deadline
from app.contracts import (
    Preference,
    ReasonCode,
    RequestItem,
    Requirement,
    StructuredRequest,
    format_date,
)

HELSINKI = ZoneInfo("Europe/Helsinki")
LLM_DEADLINE_S = 2.5  # the whole parse, fallback included, stays under 3 s
MAX_TEXT_LENGTH = 4000

ParseMode = Literal["LLM", "FALLBACK"]


class ParseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: StructuredRequest | None
    reasons: list[ReasonCode]
    clarificationQuestion: str | None = None
    ignoredInstructions: list[IgnoredInstruction] = []
    mode: ParseMode
    fallbackReason: str | None = None  # why the LLM result was not used: no_llm, timeout, invalid_output, llm_error


# --------------------------------------------------------------------------- vocabulary

_NUMBER = r"(?<![\w.,])(?P<num>\d{1,3}(?:[   ,]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)"
_UNIT_GROUPS: tuple[tuple[str, str], ...] = (
    ("l", r"litres?|liters?|litr(?:aa|an|oja|a)|ltr|l"),
    ("ml", r"ml|millilitres?|milliliters?|millilitr\w*"),
    ("dl", r"dl|decilitres?|deciliters?|desilitr\w*"),
    ("kg", r"kgs?|kilos?|kilograms?|kilogrammes?|kilogramm\w*|kilo(?:a|n)"),
    ("g", r"grams?|grammes?|gramm\w*|g"),
    ("pcs", r"pcs|pc|pieces?|packs?|packets?|packages?|bags?|bottles?|cans?|cartons?|tins?|jars?|units?|"
            r"kpl|kappale\w*|pussi\w*|pullo\w*|paketti\w*|pakkau\w*|purkki\w*|purkk\w*|tölk\w*"),
    ("cases", r"cases?|boxes|box|crates?|trays?|laatikko\w*|laatiko\w*|kotelo\w*|kartonki\w*|kartongi\w*"),
    ("portions", r"portions?|servings?|meals?|annos\w*|annok\w*|ateri\w*"),
)
_UNIT = "(?:" + "|".join(f"(?P<u_{name}>{pattern})" for name, pattern in _UNIT_GROUPS) + r")(?!\w)"
_QUANTITY_RE = re.compile(_NUMBER + r"\s*" + _UNIT, re.IGNORECASE)

_PRODUCT_ID_RE = re.compile(r"\((\d{6})\)|\b(?:product|tuote|id|no\.?|nr\.?|#)\s*:?\s*#?(\d{6})\b", re.IGNORECASE)
_FAT_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*%(?:\s*(?:fat|rasva\w*))?", re.IGNORECASE)

_REQUIREMENTS: tuple[tuple[str, Requirement], ...] = (
    (r"lactose[\s-]?free|laktoosit(?:on|tom)\w*", Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")),
    (r"gluten[\s-]?free|gluteenit(?:on|tom)\w*", Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")),
    (r"(?:milk|dairy)[\s-]?free|non[\s-]?dairy|maido(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="AM")),
    (r"eggs?[\s-]?free|muna(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="AE")),
    (r"peanut[\s-]?free|maapähkinä(?:tön|ttöm)\w*", Requirement(kind="AVOID_ALLERGEN", code="AP")),
    (r"(?:tree[\s-]?)?nut[\s-]?free|pähkinä(?:tön|ttöm)\w*", Requirement(kind="AVOID_ALLERGEN", code="AN")),
    (r"soy[\s-]?free|soya[\s-]?free|soija(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="AY")),
    (r"fish[\s-]?free|kala(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="AF")),
    (r"sesame[\s-]?free|seesami(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="AS")),
    (r"celery[\s-]?free|selleri(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="BC")),
    (r"mustard[\s-]?free|sinappi?(?:ton|ttom)\w*", Requirement(kind="AVOID_ALLERGEN", code="BM")),
    (r"vegan|vegaani\w*", Requirement(kind="CLAIM", code="VEGAN")),
    (r"vegetarian", Requirement(kind="CLAIM", code="VEGETARIAN")),
)
_REQUIREMENT_RES = tuple((re.compile(rf"\b(?:{p})\b", re.IGNORECASE), r) for p, r in _REQUIREMENTS)
_REQUIREMENT_LABELS = {
    "FREE_FROM_LACTOSE": "lactose-free", "FREE_FROM_GLUTEN": "gluten-free", "AM": "milk-free",
    "AE": "egg-free", "AP": "peanut-free", "AN": "nut-free", "AY": "soy-free", "AF": "fish-free",
    "AS": "sesame-free", "BC": "celery-free", "BM": "mustard-free", "VEGAN": "vegan", "VEGETARIAN": "vegetarian",
}

_PREFERENCES: tuple[tuple[re.Pattern, Preference], ...] = (
    (re.compile(r"\b(?:organic|luomu\w*)\b", re.IGNORECASE), Preference(kind="ORGANIC", value="organic")),
    (re.compile(r"\b(?:finnish(?:\s+origin)?|domestic|kotimai\w*)\b", re.IGNORECASE),
     Preference(kind="ORIGIN", value="FI")),
)

_WEEKDAYS_EN = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAYS_FI = ("maanantai", "tiistai", "keskiviik", "torstai", "perjantai", "lauantai", "sunnuntai")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_RE = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_PREFIX = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
_DATE_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("iso", re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")),
    ("dmy", re.compile(r"(?<![\w.,])(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})?(?![\d])")),
    ("dmon", re.compile(_DAY_PREFIX + r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r"(?!\w)", re.IGNORECASE)),
    ("mond", re.compile(_DAY_PREFIX + r"\b" + _MONTH_RE + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)),
    ("today", re.compile(r"\b(?:today|tänään|tänä\s+päivänä)\b", re.IGNORECASE)),
    ("tomorrow", re.compile(r"\b(?:tomorrow|huomenna|huomiseksi|huomiselle|huomiseen)\b", re.IGNORECASE)),
    ("overmorrow", re.compile(r"\b(?:the\s+day\s+after\s+tomorrow|ylihuomenna|ylihuomiseksi)\b", re.IGNORECASE)),
    ("weekday", re.compile(r"\b(?P<wd>" + "|".join(_WEEKDAYS_EN) + r")\b", re.IGNORECASE)),
    ("weekday_fi", re.compile(r"\b(?P<wdfi>" + "|".join(_WEEKDAYS_FI) + r")\w*", re.IGNORECASE)),
)

_STOPWORDS = {
    "of", "by", "for", "on", "before", "until", "till", "next", "this", "the", "a", "an", "some", "please",
    "pls", "delivered", "delivery", "deliver", "that", "which", "is", "are", "be", "it", "its", "it's",
    "we", "i", "need", "needed", "want", "asap", "in", "with", "both", "all", "total", "approx", "about",
    "around", "must", "should", "also", "origin", "free", "from", "to", "at", "latest", "the", "our",
    "tarvitsemme", "tarvitsen", "tarvitaan", "tarvitsisimme", "haluamme", "tilaamme", "meille", "noin",
    "mennessä", "viimeistään", "toimitettuna", "toimitus", "toimitukseen", "olla", "pitää", "täytyy",
    "myös", "kaikki", "ole", "on", "se", "ne", "jota", "joka", "mikä",
}
# Core Finnish product vocabulary (§7.2). Wider Finnish polish is T25.
_FINNISH_NOUNS: tuple[tuple[str, str], ...] = (
    ("kevytmaitojuom", "light milk drink"),
    ("maitojuom", "milk drink"),
    ("kaurajuom", "oat drink"),
    ("kevytmaito", "light milk"),
    ("rasvatonmaito", "skimmed milk"),
    ("maito", "milk"),
    ("maido", "milk"),
    ("makaron", "macaroni"),
    ("spaget", "spaghetti"),
    ("pasta", "pasta"),
    ("kerma", "cream"),
    ("juusto", "cheese"),
    ("jogurt", "yoghurt"),
    ("rahka", "quark"),
    ("kahvi", "coffee"),
    ("riisi", "rice"),
    ("jauho", "flour"),
    ("kananmun", "eggs"),
    ("peruna", "potatoes"),
    ("leipä", "bread"),
    ("leivä", "bread"),
    ("kevyt", "light"),
)
_DAIRY_RE = re.compile(r"\b(?:milk|cream|cheese|butter|yogh?urt|quark)\b", re.IGNORECASE)
_PLANT_RE = re.compile(r"\b(?:oat|soy|soya|almond|coconut|rice|pea|plant|vegan|vegetable)\b", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CHUNK_SPLIT = re.compile(r",\s+|;\s*|\s+(?:and|ja|sekä|plus|\+|&)\s+", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[()\[\]{}.,:;!?\"“”«»—–/]+")

UNIT_WORDS = "litres, kilograms, pieces or cases"


# --------------------------------------------------------------------------- helpers


def _to_decimal(raw: str) -> Decimal:
    if re.fullmatch(r"\d{1,3}(?:[   ,]\d{3})+", raw):
        return Decimal(re.sub(r"[   ,]", "", raw))
    return Decimal(raw.replace(",", "."))


def _unit_of(match: re.Match) -> str:
    return next(name for name, _ in _UNIT_GROUPS if match.group(f"u_{name}"))


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _next_weekday(today: date, weekday: int) -> date:
    return today + timedelta(days=(weekday - today.weekday()) % 7)


@dataclass
class _DatePhrase:
    value: date
    kind: str
    start: int
    end: int


def _find_dates(text: str, today: date) -> list[_DatePhrase]:
    found: list[_DatePhrase] = []
    for kind, pattern in _DATE_RES:
        for m in pattern.finditer(text):
            if any(m.start() < f.end and f.start < m.end() for f in found):
                continue
            try:
                if kind == "iso":
                    value = date(int(m["y"]), int(m["m"]), int(m["d"]))
                elif kind in ("dmy", "dmon", "mond"):
                    month = int(m["m"]) if kind == "dmy" else _MONTHS.index(m["mon"].lower()[:3]) + 1
                    year = int(m["y"]) if kind == "dmy" and m["y"] else today.year
                    value = date(year, month, int(m["d"]))
                    if not (kind == "dmy" and m["y"]) and value < today:
                        value = date(year + 1, month, int(m["d"]))
                elif kind == "today":
                    value = today
                elif kind == "tomorrow":
                    value = today + timedelta(days=1)
                elif kind == "overmorrow":
                    value = today + timedelta(days=2)
                elif kind == "weekday":
                    value = _next_weekday(today, _WEEKDAYS_EN.index(m["wd"].lower()))
                else:
                    value = _next_weekday(today, _WEEKDAYS_FI.index(m["wdfi"].lower()[: _fi_len(m["wdfi"])]))
            except ValueError:
                continue
            found.append(_DatePhrase(value, kind, m.start(), m.end()))
    found.sort(key=lambda f: f.start)
    return found


def _fi_len(word: str) -> int:
    lower = word.lower()
    return next(len(stem) for stem in _WEEKDAYS_FI if lower.startswith(stem))


def _translate(token: str) -> str:
    """Finnish product noun (any inflection) -> English; anything else (brands, names) as written."""
    lower = token.lower()
    for stem, english in _FINNISH_NOUNS:
        if lower.startswith(stem):
            return english
    return token


@dataclass
class _ItemDraft:
    quantity: Decimal | None
    unit: str | None
    raw_quantity: str
    what: str | None
    product_id: str | None
    requirements: list[Requirement] = field(default_factory=list)
    preferences: list[Preference] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _extract(fragment: str) -> tuple[str, list[Requirement], list[Preference], str | None, list[str]]:
    """Strip requirement/preference/date/id phrases from a fragment; return (rest, reqs, prefs, id, packs)."""
    requirements: list[Requirement] = []
    preferences: list[Preference] = []
    product_id = None
    packs: list[str] = []

    def take_id(m: re.Match) -> str:
        nonlocal product_id
        product_id = product_id or m.group(1) or m.group(2)
        return " "

    rest = _PRODUCT_ID_RE.sub(take_id, fragment)
    for pattern, requirement in _REQUIREMENT_RES:
        if pattern.search(rest):
            if requirement not in requirements:
                requirements.append(requirement)
            rest = pattern.sub(" ", rest)
    for pattern, preference in _PREFERENCES:
        if pattern.search(rest):
            preferences.append(preference)
            rest = pattern.sub(" ", rest)

    def take_fat(m: re.Match) -> str:
        preferences.append(Preference(kind="FAT_PERCENT", value=m.group(1).replace(",", ".")))
        return " "

    rest = _FAT_RE.sub(take_fat, rest)

    def take_pack(m: re.Match) -> str:
        packs.append(f"{_plain(_to_decimal(m['num']))} {_unit_of(m)}")
        return " "

    rest = _QUANTITY_RE.sub(take_pack, rest)
    for _kind, pattern in _DATE_RES:
        rest = pattern.sub(" ", rest)
    return rest, requirements, preferences, product_id, packs


def _what(rest: str) -> str | None:
    tokens = [t for t in _PUNCTUATION.sub(" ", rest).split() if t.lower().strip("'’") not in _STOPWORDS]
    tokens = [_translate(t) for t in tokens if not re.fullmatch(r"[-®™'’]+", t)]
    words: list[str] = []
    for token in tokens:
        if not words or words[-1] != token:
            words.append(token)
    return " ".join(words).strip(" -") or None


@dataclass
class _Fallback:
    request: StructuredRequest | None
    question: str | None
    safety_question: str | None  # unsupported unit or contradiction: wins over any LLM result
    explicit_date: date | None
    items: list[_ItemDraft]


def _split_chunks(sentence: str) -> list[str]:
    return [c for c in _CHUNK_SPLIT.split(sentence) if c and c.strip()]


def _fallback(text: str, today: date, ignored: list[IgnoredInstruction]) -> _Fallback:
    # Request text is data: instruction-like spans are cut out before anything else reads the text.
    clean = text
    for finding in sorted(ignored, key=lambda f: f.start, reverse=True):
        clean = clean[: finding.start] + " . " + clean[finding.end :]

    dates = _find_dates(clean, today)
    explicit = dates[0] if dates else None

    items: list[_ItemDraft] = []
    extra_requirements: list[Requirement] = []
    for sentence in _SENTENCE_SPLIT.split(clean):
        if not _QUANTITY_RE.search(sentence):
            # A sentence without a quantity can still carry a hard requirement ("It must be gluten-free").
            for pattern, requirement in _REQUIREMENT_RES:
                if pattern.search(sentence) and requirement not in extra_requirements:
                    extra_requirements.append(requirement)
            continue
        fragments: list[str] = []
        pending = ""
        for chunk in _split_chunks(sentence):
            if _QUANTITY_RE.search(chunk):
                fragments.append(f"{pending} {chunk}".strip())
                pending = ""
            elif fragments:
                fragments[-1] = f"{fragments[-1]} {chunk}"
            else:
                pending = f"{pending} {chunk}".strip()
        for fragment in fragments:
            first = _QUANTITY_RE.search(fragment)
            assert first is not None
            before, after = fragment[: first.start()], fragment[first.end() :]
            rest_after, req_a, pref_a, id_a, packs = _extract(after)
            rest_before, req_b, pref_b, id_b, _ = _extract(before)
            requirements = req_b + [r for r in req_a if r not in req_b]
            what = _what(rest_after) or _what(_strip_lead(rest_before))
            unit = _unit_of(first)
            value = _to_decimal(first["num"])
            notes: list[str] = []
            if unit in ("g", "ml", "dl"):
                factor = {"g": Decimal(1000), "ml": Decimal(1000), "dl": Decimal(10)}[unit]
                converted = value / factor
                target = "kg" if unit == "g" else "l"
                notes.append(f"{_plain(value)} {unit} read as {_plain(converted)} {target}")
                value, unit = converted, target
            preferences = pref_b + pref_a + [Preference(kind="PACK_SIZE", value=p) for p in packs]
            items.append(_ItemDraft(value, unit, first.group(0), what, id_a or id_b, requirements, preferences, notes))

    for item in items:
        for requirement in extra_requirements:
            if requirement not in item.requirements:
                item.requirements.append(requirement)

    # O6: exactly one question, the most important first.
    safety_question = None
    question = None
    if not items:
        rest, found_requirements, *_ = _extract(clean)
        stripped = _strip_lead(rest)
        # Name the product only when the text reads as a purchase ("we need …", "gluten-free …").
        what = _what(stripped) if (stripped != rest or found_requirements) else None
        question = (
            f"How much {what} do you need, in {UNIT_WORDS}?" if what
            else "What do you need, how much, and by when?"
        )
    for item in items:
        if item.unit == "portions":
            safety_question = (
                f"Portions are not an order unit: how many {UNIT_WORDS} of {item.what or 'the product'} "
                f"do you need for {_plain(item.quantity)} portions?"
            )
            break
    if safety_question is None:
        for item in items:
            if item.what and _contradiction(item.what, item.requirements):
                safety_question = _contradiction(item.what, item.requirements)
                break
    if question is None and safety_question is None:
        for item in items:
            if not item.what:
                question = f"Which product do you need {item.raw_quantity} of?"
                break

    request = None
    if items and question is None and safety_question is None:
        requested = explicit.value if explicit else today + timedelta(days=1)
        assumptions: list[str] = []
        if explicit is None:
            assumptions.append(f"No delivery date given: assumed {format_date(requested)} (tomorrow)")
        elif explicit.kind in ("weekday", "weekday_fi"):
            assumptions.append(f"Delivered on or before {format_date(requested)}")
        for item in items:
            assumptions.extend(item.notes)
            assumptions.extend(_item_assumptions(item.what or "", item.preferences))
        if len(extra_requirements) and len(items) > 1:
            labels = ", ".join(_REQUIREMENT_LABELS.get(r.code, r.code) for r in extra_requirements)
            assumptions.append(f"Applied to every item: {labels}")
        request = StructuredRequest(
            requestText=text,
            requestedDate=requested,
            items=[
                RequestItem(
                    itemId=f"I{n}",
                    what=item.what or "",
                    quantity=item.quantity,
                    unit=item.unit,
                    productId=item.product_id,
                    requirements=item.requirements,
                    preferences=item.preferences,
                )
                for n, item in enumerate(items, start=1)
            ],
            assumptions=list(dict.fromkeys(assumptions)),
        )
    return _Fallback(request, question, safety_question, explicit.value if explicit else None, items)


def _strip_lead(text: str) -> str:
    return re.sub(
        r"^.*?\b(?:we need|i need|we'd like|i'd like|we want|i want|please send|send us|order|"
        r"tarvitsemme|tarvitsen|tarvitaan|haluamme|tilaamme)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )


def _item_assumptions(what: str, preferences: list[Preference]) -> list[str]:
    lower = what.lower()
    out: list[str] = []
    dairy_milk = "milk" in lower and not _PLANT_RE.search(lower)
    if dairy_milk and "milk drink" not in lower:
        out.append("Milk drinks accepted")
    if dairy_milk and not any(p.kind == "FAT_PERCENT" for p in preferences):
        out.append("Any fat %")
    if "milk" in lower or "drink" in lower:
        out.append("UHT or fresh")
    return out


def _contradiction(what: str, requirements: list[Requirement]) -> str | None:
    if _DAIRY_RE.search(what) and not _PLANT_RE.search(what):
        for requirement in requirements:
            if (requirement.kind, requirement.code) in (("AVOID_ALLERGEN", "AM"), ("CLAIM", "VEGAN")):
                label = _REQUIREMENT_LABELS[requirement.code]
                return (
                    f"{what[:1].upper()}{what[1:]} is a dairy product, so it cannot also be {label}: "
                    f"do you need lactose-free {what}, or a milk-free alternative such as oat drink?"
                )
    return None


def _one_question(text: str) -> str:
    text = " ".join(text.split())
    mark = text.find("?")
    return text[: mark + 1] if mark >= 0 else text.rstrip(".") + "?"


# --------------------------------------------------------------------------- LLM path


def _from_llm(raw: dict, text: str, today: date, fallback: _Fallback) -> tuple[StructuredRequest | None, str | None]:
    question = raw.get("clarificationQuestion")
    if isinstance(question, str) and question.strip():
        return fallback.request, _one_question(question)
    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("no items")
    items = []
    for n, entry in enumerate(raw_items, start=1):
        if not isinstance(entry, dict):
            raise ValueError("item is not an object")
        quantity = entry.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int | float | str):
            raise ValueError("bad quantity")
        try:
            quantity = Decimal(str(quantity))
        except InvalidOperation as exc:
            raise ValueError("bad quantity") from exc
        product_id = entry.get("productId")
        # Never invent facts: a product number counts only if the buyer actually wrote it.
        if not (isinstance(product_id, str) and re.fullmatch(r"\d{6}", product_id) and product_id in text):
            product_id = None
        items.append(
            {
                "itemId": f"I{n}",
                "what": entry.get("what"),
                "quantity": quantity,
                "unit": entry.get("unit"),
                "productId": product_id,
                "requirements": entry.get("requirements") or [],
                "preferences": entry.get("preferences") or [],
            }
        )
    requested = date.fromisoformat(str(raw.get("requestedDate")))
    if fallback.explicit_date is not None:
        requested = fallback.explicit_date  # date arithmetic is deterministic, never the LLM's
    if requested < today:
        raise ValueError("requested date in the past")
    assumptions = [a.strip()[:200] for a in raw.get("assumptions") or [] if isinstance(a, str) and a.strip()][:10]
    request = StructuredRequest.model_validate(
        {"requestText": text, "requestedDate": requested, "items": items, "assumptions": assumptions}
    )
    # A hard requirement the deterministic reading found is never dropped by the LLM.
    if fallback.request is not None and len(fallback.request.items) == len(request.items):
        merged = []
        for llm_item, det_item in zip(request.items, fallback.request.items, strict=True):
            extra = [r for r in det_item.requirements if r not in llm_item.requirements]
            merged.append(llm_item.model_copy(update={"requirements": [*llm_item.requirements, *extra]}))
        request = request.model_copy(update={"items": merged})
    return request, None


# --------------------------------------------------------------------------- entry point


def parse_request(
    text: str,
    now: datetime,
    interpreter: LlmInterpreter | None = None,
    deadline_s: float = LLM_DEADLINE_S,
) -> ParseResult:
    """Parse buyer text into a StructuredRequest. `now` must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(HELSINKI).date()
    ignored = detect_instructions(text)
    reasons = [ReasonCode.INSTRUCTION_IGNORED] if ignored else []

    if len(text) > MAX_TEXT_LENGTH:
        return ParseResult(
            request=None,
            reasons=[*reasons, ReasonCode.CLARIFICATION_NEEDED],
            clarificationQuestion="Could you shorten the request to the products, quantities and delivery date?",
            ignoredInstructions=ignored,
            mode="FALLBACK",
            fallbackReason="too_long",
        )

    fallback = _fallback(text, today, ignored)
    request, question = fallback.request, fallback.question
    mode: ParseMode = "FALLBACK"
    fallback_reason: str | None = "no_llm"
    if interpreter is not None:
        try:
            raw = call_with_deadline(interpreter, text, today, deadline_s)
            if not isinstance(raw, dict):
                raise ValueError("LLM result is not an object")
            request, question = _from_llm(raw, text, today, fallback)
            mode, fallback_reason = "LLM", None
        except TimeoutError:
            fallback_reason = "timeout"
        except (ValidationError, ValueError, TypeError, KeyError):
            fallback_reason = "invalid_output"
        except Exception:  # network, auth, provider errors: the demo survives an LLM outage
            fallback_reason = "llm_error"

    if fallback.safety_question is not None:
        question = fallback.safety_question
    if question is None and request is not None:
        question = next(
            (q for item in request.items if (q := _contradiction(item.what, item.requirements))), None
        )
    if question is not None:
        reasons.append(ReasonCode.CLARIFICATION_NEEDED)
    return ParseResult(
        request=request,
        reasons=reasons,
        clarificationQuestion=question,
        ignoredInstructions=ignored,
        mode=mode,
        fallbackReason=fallback_reason,
    )
