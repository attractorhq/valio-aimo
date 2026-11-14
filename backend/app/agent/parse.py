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

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agent.injection import IgnoredInstruction, detect_instructions
from app.agent.llm import LlmInterpreter, call_with_deadline
from app.catalog.vocab import (
    FI_SV_TO_EN_SYNONYMS,
    FORMS_BY_LABEL,
    KNOWN_BRANDS,
    extract_requested_base,
    extract_requested_form,
    resolve_query_concepts,
    split_query_alternatives,
)
from app.contracts import (
    Preference,
    ReasonCode,
    RequestItem,
    Requirement,
    StructuredRequest,
    format_date,
)
from app.policy.instructions import detect_instruction

logger = logging.getLogger("app.agent.injection")
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
    ("l", r"litres?|liters?|litr(?:aa|an|oja|a)?|ltr|l"),
    ("ml", r"ml|millilitres?|milliliters?|millilitr\w*"),
    ("dl", r"dl|decilitres?|deciliters?|desilitr\w*"),
    ("kg", r"kgs?|kilos?|kilograms?|kilogrammes?|kilogramm\w*|kilo(?:a|n)?"),
    ("g", r"grams?|grammes?|gramm\w*|g"),
    ("pcs", r"pcs|pc|pieces?|packs?|packets?|packages?|bags?|bottles?|cans?|cartons?|tins?|jars?|units?|"
            r"kpl|kappale\w*|pussi\w*|pullo\w*|paketti\w*|pakkau\w*|purkki\w*|purkk\w*|tölk\w*|tlk|"
            r"säkki\w*|säki\w*|säkk\w*|rasia\w*|rasio\w*|pötkö\w*"),
    ("cases", r"cases?|boxes|box|crates?|trays?|laatikko\w*|laatiko\w*|ltk|kotelo\w*|kartonki\w*|kartongi\w*|"
              r"tarjotin\w*|tarjottim\w*"),
    ("portions", r"portions?|servings?|meals?|annos\w*|annok\w*|ateri\w*"),
)
_UNIT = "(?:" + "|".join(f"(?P<u_{name}>{pattern})" for name, pattern in _UNIT_GROUPS) + r")(?!\w)"
_QUANTITY_RE = re.compile(_NUMBER + r"\s*" + _UNIT, re.IGNORECASE)

# T95 Scope 3: Pack-size phrases ("1 kg bags", "in 5 kg packs", "5 kg pussit") are a pack preference on the
# current item, never a new item.
_PACK_UNIT_GROUPS: tuple[tuple[str, str], ...] = _UNIT_GROUPS[:5]
_PACK_UNIT = "(?:" + "|".join(f"(?P<pu_{name}>{pattern})" for name, pattern in _PACK_UNIT_GROUPS) + r")(?!\w)"
_PACK_CONTAINER_WORDS = (
    r"bags?|packs?|packets?|packages?|cartons?|bottles?|cans?|tins?|jars?|tubs?|buckets?|pails?|boxes|box|"
    r"cases?|crates?|trays?|sacks?|pouches?|pouch|containers?|drums?|units?|pieces?|pcs|pc|"
    r"pussi\w*|pussei\w*|pakkau\w*|paketti\w*|paketei\w*|purkki\w*|purkk\w*|purkei\w*|tölk\w*|tlk|"
    r"pullo\w*|pulloi\w*|rasia\w*|rasio\w*|laatikko\w*|laatiko\w*|ltk|kotelo\w*|kartonki\w*|kartongi\w*|"
    r"säkki\w*|säki\w*|säkk\w*|säkei\w*|ämpäri\w*|ämpärei\w*|tonkka\w*|tonka\w*|tonki\w*|kanisteri\w*|"
    r"astia\w*|astio\w*|pötkö\w*|kpl|kappale\w*"
)
_PACK_PHRASE_RE = re.compile(
    r"(?:(?:\b(?:packed|packaged|pakattuna)\s+)?\bin\s+)?"
    + _NUMBER
    + r"\s*[-–]?\s*"
    + _PACK_UNIT
    + r"(?::n)?\s+(?:(?:"
    + _PACK_CONTAINER_WORDS
    + r")(?!\w))",
    re.IGNORECASE,
)
_BARE_CONTAINER_RE = re.compile(r"^(?:(?:\bin\s+)?(?:" + _PACK_CONTAINER_WORDS + r")[\s-]*)+$", re.IGNORECASE)

_PRODUCT_ID_RE = re.compile(r"\((\d{6})\)|\b(?:product|tuote|id|no\.?|nr\.?|#)\s*:?\s*#?(\d{6})\b", re.IGNORECASE)
_FAT_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*%(?:\s*(?:fat|rasva\w*))?", re.IGNORECASE)

# Hard requirements: (requirements, English words, Finnish stems). Every phrasing below maps to the same
# requirements: "X-free", "no X", "without X", "free of X", "X allergy", Finnish "X-ton/-tön",
# "ilman X", "ei X", "X-allergia". Order matters: peanut before nut, kananmuna before muna.
_AN = Requirement(kind="AVOID_ALLERGEN", code="AN")
_AP = Requirement(kind="AVOID_ALLERGEN", code="AP")
_ALLERGEN_VOCAB: tuple[tuple[tuple[Requirement, ...], str | None, str | None], ...] = (
    ((Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE"),), r"lactose", r"laktoosi"),
    ((Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN"),), r"gluten", r"gluteeni"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AM"),), r"milk|dairy", r"maido|maito|maitotuottee"),
    ((_AP,), r"peanuts?", r"maapähkinä|maapähkinö"),
    ((_AN,), r"(?:tree[\s-]?)?nuts?", None),
    # Finnish "pähkinä" covers peanuts as well as tree nuts: both EU-14 codes are required.
    ((_AN, _AP), None, r"pähkinä|pähkinö"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AE"),), r"eggs?", r"kananmuna|kananmuni|muna|muni"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AF"),), r"fish", r"kala"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AC"),), r"crustaceans?|shellfish", r"äyriäise|äyriäi"),
    ((Requirement(kind="AVOID_ALLERGEN", code="UM"),), r"molluscs?|mollusks?", r"nilviäise|nilviäi"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AY"),), r"soy|soya", r"soija"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AS"),), r"sesame", r"seesami"),
    ((Requirement(kind="AVOID_ALLERGEN", code="BC"),), r"celery", r"selleri"),
    ((Requirement(kind="AVOID_ALLERGEN", code="BM"),), r"mustard", r"sinappi|sinapi"),
    ((Requirement(kind="AVOID_ALLERGEN", code="NL"),), r"lupin", r"lupiini|lupiin"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AU"),), r"sulphites?|sulfites?", r"sulfiiti|sulfiit"),
    ((Requirement(kind="AVOID_ALLERGEN", code="UW"),), r"wheat", r"vehnä"),
)
_NEGATION_EN = (r"(?:no|without|free\s+(?:of|from)|not\s+containing|contains?\s+no|zero|avoid(?:ing)?|"
                r"excluding|exclude)\s+(?:any\s+)?(?:added\s+|traces\s+of\s+)?")
_NEGATION_FI = r"(?:ilman|ei)\s+"


def _requirement_pattern(en: str | None, fi: str | None) -> str:
    parts: list[str] = []
    if en:
        parts += [
            rf"\b(?:{en})[\s-]?free\b",
            rf"\b{_NEGATION_EN}(?:{en})\b",
            rf"\b(?:{en})\s+allerg(?:y|ies|ic)\b|\ballergic\s+to\s+(?:{en})\b",
        ]
    if fi:
        parts += [
            rf"\b(?:{fi})(?:ton|tön|tto|ttö)\w*",
            rf"\b{_NEGATION_FI}(?:{fi})\w*",
            rf"\b(?:{fi})\w*allergi\w*",
        ]
    return "|".join(parts)


_REQUIREMENTS: tuple[tuple[str, tuple[Requirement, ...]], ...] = (
    *((_requirement_pattern(en, fi), requirements) for requirements, en, fi in _ALLERGEN_VOCAB),
    (r"\bnon[\s-]?dairy\b", (Requirement(kind="AVOID_ALLERGEN", code="AM"),)),
    (r"\b(?:vegan|vegaani\w*)\b", (Requirement(kind="CLAIM", code="VEGAN"),)),
    (r"\bvegetarian\b", (Requirement(kind="CLAIM", code="VEGETARIAN"),)),
)
_REQUIREMENT_RES = tuple((re.compile(p, re.IGNORECASE), r) for p, r in _REQUIREMENTS)
_DIET_RES = (
    (re.compile(r"\b(?:keliaki\w*|keliaak(?:ikko|iko)\w*|keliakist\w*|gluteeni-?allergi\w*|co?eliacs?)\b",
                re.IGNORECASE),
     (Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN"),)),
    (re.compile(r"\b(?:maito-?allergi\w*|maidottom(?:alle|ille)|milk[\s-]+allergic)\b", re.IGNORECASE),
     (Requirement(kind="AVOID_ALLERGEN", code="AM"),)),
    (re.compile(r"\b(?:kanan)?muna-?allergi\w*\b", re.IGNORECASE),
     (Requirement(kind="AVOID_ALLERGEN", code="AE"),)),
    (re.compile(r"\bpähkinä-?allergi\w*\b", re.IGNORECASE), (_AN, _AP)),
    (re.compile(r"\blaktoosi[\s-]?intolerans\w*\b", re.IGNORECASE),
     (Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE"),)),
)
_REQUIREMENT_LABELS = {
    "FREE_FROM_LACTOSE": "lactose-free", "FREE_FROM_GLUTEN": "gluten-free", "AM": "milk-free",
    "AE": "egg-free", "AP": "peanut-free", "AN": "nut-free", "AY": "soy-free", "AF": "fish-free",
    "AC": "crustacean-free", "UM": "mollusc-free", "AS": "sesame-free", "BC": "celery-free",
    "BM": "mustard-free", "NL": "lupin-free", "AU": "sulphite-free", "UW": "wheat-free",
    "VEGAN": "vegan", "VEGETARIAN": "vegetarian",
}
# A negation the vocabulary does not know is never dropped silently: it becomes one clarifying question.
_UNKNOWN_NEGATION_RE = re.compile(
    r"\b(?:no|without|ilman)\s+(?P<word>[^\W\d_][\w-]*)|\b(?P<free>[^\W\d_]+)-free\b", re.IGNORECASE
)
_BENIGN_NEGATED = {
    "later", "sooner", "more", "less", "longer", "matter", "need", "problem", "problems", "delay", "rush",
    "hurry", "worries", "worry", "extra", "other", "substitute", "substitutes", "substitution",
    "substitutions", "alternatives", "changes", "change", "charge", "fee", "cost", "costs", "thanks",
    "muuta", "viivettä", "korvaavia", "korvaavaa", "vaihtoa", "lisämaksua", "lisäkuluja", "kiirettä",
}
_BENIGN_NEGATION_RE = re.compile(
    r"\b(?:no|without|ilman)\s+(?:" + "|".join(sorted(_BENIGN_NEGATED, key=len, reverse=True)) + r")\b(?:\s+than)?",
    re.IGNORECASE,
)
# T74: a brand or product name is a name ("Oatly No Sugars", "Coca-Cola Zero"): a known brand followed by its
# capitalised product-line words, or a capitalised name mid-sentence followed by "No"/"Zero" and a capitalised
# word. Words inside it are never exclusions; the name stays in the item for the search.
_BRAND_PHRASE_RE = re.compile(
    r"(?<![\w-])(?i:" + "|".join(re.escape(b) for b in sorted(KNOWN_BRANDS, key=len, reverse=True)) + r")(?![\w-])"
    r"(?:[ \t]+[A-ZÅÄÖ][\w'’®™&+-]*)*"
    # Mid-sentence, or first in an item fragment (" Planto No Added Sugar …"), never a sentence's first word.
    r"|(?:(?<=[^\W\d_][ \t])|(?<=^[ \t]))[A-ZÅÄÖ][\w'’®™-]+[ \t]+(?:No|Zero)[ \t]+[A-ZÅÄÖ][\w'’®™-]*"
)
# T74: certifications are wishes about the label, not part of the product name. Until they are checked as
# requirements they are kept as a LABEL preference (labelled products rank first) and said in the assumptions.
_CERTIFICATIONS: tuple[tuple[re.Pattern, str, str], ...] = tuple(
    (
        re.compile(
            r"\b(?:certified\s+)?(?:" + pattern + r")(?:[\s-]+(?:certified|certification|labell?ed|label|"
            r"sertifioi\w*|merki\w*))?(?!\w)",
            re.IGNORECASE,
        ),
        code,
        label,
    )
    for pattern, code, label in (
        (r"MSC", "MARINE_STEWARDSHIP_COUNCIL_LABEL", "MSC"),
        (r"ASC", "AQUACULTURE_STEWARDSHIP_COUNCIL", "ASC"),
        (r"fair[\s-]?trade|reilu(?:n|a)?\s+kaup\w*", "FAIR_TRADE_MARK", "Fairtrade"),
    )
)
_CERTIFICATION_NAMES = {code: label for _pattern, code, label in _CERTIFICATIONS}
# Purpose clauses ("for the school", "for our kitchen") describe the buyer, not the product.
_PURPOSE_RE = re.compile(
    r"\bfor\s+(?:the|our|a|an|my|this|their)?\s*(?!(?:cooking|whipping|frying|drinking)\b)[^\W\d_]+"
    r"(?:\s+(?:kitchen|canteen|cafeteria|restaurant|"
    r"staff|team|event|lunch|dinner|breakfast|guests|week|weekend))?",
    re.IGNORECASE,
)
_CREAM_PURPOSE_RES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b(?:cream\s+for\s+cooking|kerma(?:a|n)?\s+ruoanlaittoon)\b", re.IGNORECASE), "cooking cream"),
    (re.compile(r"\b(?:cream\s+for\s+whipping|kerma(?:a|n)?\s+vatkaukseen)\b", re.IGNORECASE), "whipping cream"),
)

_PREFERENCES: tuple[tuple[re.Pattern, Preference], ...] = (
    (re.compile(r"\b(?:certified\s+)?(?:organic|luomu\w*)(?:[\s-]+(?:certified|sertifioi\w*))?\b", re.IGNORECASE),
     Preference(kind="ORGANIC", value="organic")),
    (re.compile(r"\b(?:finnish(?:\s+origin)?|domestic|kotimai\w*)\b", re.IGNORECASE),
     Preference(kind="ORIGIN", value="FI")),
)

_WEEKDAYS_EN = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAYS_FI = ("maanantai", "tiistai", "keskiviik", "torstai", "perjantai", "lauantai", "sunnuntai")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_RE = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
             r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY_PREFIX = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
_MONTHS_FI = ("tammi", "helmi", "maalis", "huhti", "touko", "kesä", "heinä", "elo", "syys", "loka", "marras",
              "joulu")
_DATE_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("iso", re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")),
    # "16. marraskuuta", "16 marraskuuta 2026"
    ("dfi", re.compile(r"(?<![\w.,])(?P<d>\d{1,2})\.?\s+(?P<fimon>" + "|".join(_MONTHS_FI) + r")kuu\w*"
                       r"(?:\s+(?P<y>\d{4}))?", re.IGNORECASE)),
    ("dmy", re.compile(r"(?<![\w.,])(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})?(?![\d])")),
    ("dmon", re.compile(_DAY_PREFIX + r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r"(?!\w)", re.IGNORECASE)),
    ("mond", re.compile(_DAY_PREFIX + r"\b" + _MONTH_RE + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)),
    ("today", re.compile(r"\b(?:today|tänään|tänä\s+päivänä)\b", re.IGNORECASE)),
    ("tomorrow", re.compile(r"\b(?:tomorrow|huomenna|huomiseksi|huomiselle|huomiseen)\b", re.IGNORECASE)),
    ("overmorrow", re.compile(r"\b(?:the\s+day\s+after\s+tomorrow|ylihuomenna|ylihuomiseksi)\b", re.IGNORECASE)),
    ("weekday", re.compile(r"\b(?P<wd>" + "|".join(_WEEKDAYS_EN)
                           + r")\b(?:\s+(?P<wd_day>\d{1,2})(?:st|nd|rd|th)?(?!\w))?", re.IGNORECASE)),
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
    "as", "usual", "thanks", "thank", "you", "hi", "hello", "hey", "kiitos", "moi", "hei", "kuten", "yleensä",
    "tavallista", "aina", "tehty", "tehtyä", "tehtyjä", "valmistettu", "valmistettua",
    "ensi", "seuraavaksi", "vähintään", "yhteensä", "olisi", "tarvittaisiin", "tilataan", "haluaisimme",
    "tilaisimme", "toimittakaa", "lähettäkää", "kiitti", "tällä", "kertaa",
    # Time, urgency, meal and vagueness words describe the delivery or the occasion, never the product.
    "immediately", "urgently", "urgent", "automatically", "now", "right", "away", "quickly", "soon", "possible",
    "lunch", "dinner", "breakfast", "brunch", "supper", "severe", "serious",
    "something", "anything", "whatever", "preferably", "ideally",
    "heti", "pian", "nopeasti", "kiireellisesti", "kiireesti", "pikaisesti", "välittömästi",
    "automaattisesti", "mahdollisimman", "lounaaksi", "lounaalle", "päivälliseksi", "aamiaiseksi",
    "illalliseksi", "välipalaksi", "jälkiruoaksi", "jälkiruuaksi", "vakava", "vakavan",
    "jotain", "jotakin", "mitä", "tahansa",
    "mieluiten", "ensisijaisesti", "mielellään", "toiveena", "suoraan", "valmiiksi", "valmiina", "mukaan", "lisäksi",
    "maybe", "perhaps", "approximately", "ehkä", "roughly",
}
_HEDGE_RE = re.compile(r"\b(?:maybe|perhaps|some|something|anything|whatever|approximately|approx|about|around|"
                       r"roughly|noin|ehkä|jotain|jotakin)\b", re.IGNORECASE)
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
    ("mysli", "muesli"),
    ("kerma", "cream"),
    ("juusto", "cheese"),
    ("jogurt", "yoghurt"),
    ("jugurt", "yogurt"),
    ("rahka", "quark"),
    ("kahvi", "coffee"),
    ("riisi", "rice"),
    ("jauho", "flour"),
    ("kananmun", "eggs"),
    ("peruna", "potatoes"),
    ("peruno", "potatoes"),
    ("leipä", "bread"),
    ("leivä", "bread"),
    ("kevyt", "light"),
)
# Whole-word Finnish product nouns in their common inflections (nominative, partitive, genitive, illative):
# a stem prefix is not safe for these ("voi" is also a verb, "lohko" is not salmon, "kermaviili" is not
# cream). Checked before the stems.
_FINNISH_FORMS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), english) for pattern, english in (
        # T52: stated attributes stay in the item ("suolattua voita" is salted butter, not "salt butter").
        (r"suolat(?:tu|tua|tun|tuja|tuna)|normaalisuolai\w*", "salted"),
        (r"suolaton|suolatonta|suolattoman|suolattomia|suolattomana", "unsalted"),
        (r"kylmäsavustet(?:tu|tua|tun|tuja)", "cold-smoked"),
        (r"lämminsavustet(?:tu|tua|tun|tuja)", "hot-smoked"),
        (r"savustet(?:tu|tua|tun|tuja)", "smoked"),
        (r"pakastet(?:tu|tua|tun|tuja)", "frozen"),
        (r"tuore|tuoretta|tuoreen|tuoreita|tuoreet", "fresh"),
        # T87: stated product forms (grated, sliced, block, shredded, cubed, whole, powder).
        (r"(?:tuore)?raaste(?:ena|tta|en|et|ita|eseen|ella|elta|essa|eksi)?|raastet(?:tu|tua|tun|tuja|tuina|tuna|ut)?",
         "grated"),
        (r"viipale(?:ena|ina|ita|et|en|eseen|ella|eksi)?|viipaloi(?:tu|tua|tun|tuja|tuna|dut)?|"
         r"siivu(?:na|ina|ja|t|n)?|siivutet(?:tu|tua|tun|tuja|tuna)?", "sliced"),
        (r"palana|paloina|kimpale(?:ena|ina|ita|et|en)?|tahko(?:na|ja|t|n)?", "block"),
        (r"suikale(?:ena|ina|ita|et|en|eseen|ella)?|suikaloi(?:tu|tua|tun|tuja|tuna|dut)?", "shredded"),
        (r"kuutio(?:na|ina|ita|t|n|iksi)?|kuutioi(?:tu|tua|tun|tuja|tuna|dut)?", "cubed"),
        (r"kokonai(?:nen|sena|sia|set|sta|sen|siksi)", "whole"),
        (r"jauhe(?:ena|tta|en|et|ita|eseen|eksi)?", "powder"),
        (r"juusto-?raaste\w*|raaste-?juust\w*", "grated cheese"),
        (r"emmental-?raaste\w*|emmentaal-?raaste\w*", "grated Emmental cheese"),
        (r"mozzarella-?raaste\w*", "grated mozzarella cheese"),
        (r"cheddar-?raaste\w*", "grated cheddar cheese"),
        (r"juusto-?viipale\w*|viipale-?juust\w*", "sliced cheese"),
        (r"emmental-?viipale\w*|emmentaal-?viipale\w*", "sliced Emmental cheese"),
        (r"juusto-?kuutio\w*|kuutio-?juust\w*", "cubed cheese"),
        (r"feta-?kuutio\w*|salaattijuusto-?kuutio\w*", "cubed feta cheese"),
        (r"pala-?juust\w*|juusto-?pala\w*|juusto-?kimpale\w*", "block cheese"),
        (r"broileri-?suikale\w*|kana-?suikale\w*", "shredded chicken"),
        (r"liha-?suikale\w*|naudan-?suikale\w*|porsaan-?suikale\w*", "shredded meat"),
        (r"kinkku-?suikale\w*", "shredded ham"),
        (r"kasvis-?suikale\w*", "shredded vegetables"),
        (r"kinkku-?viipale\w*", "sliced ham"),
        (r"lohi-?viipale\w*", "sliced salmon"),
        (r"lohi-?kuutio\w*", "cubed salmon"),
        (r"peruna-?kuutio\w*", "cubed potatoes"),
        (r"liha-?kuutio\w*", "cubed meat"),
        (r"(?:kirnu|meijeri|ruoka)?voi(?:ta|n|hin|ksi)?", "butter"),
        (r"kermavaah\w*|(?:kuohu|vispi)kerm\w*", "whipping cream"),
        (r"(?:ruoka|ruoanlaitto)kerm\w*", "cooking cream"),
        (r"kahvikerm\w*", "coffee cream"),
        (r"kevytkerm\w*", "low-fat cream"),
        (r"kermaviil\w*|smetan\w*|ranskankerm\w*", "sour cream"),
        # T74: the most specific type, never the first part ("kermajuusto" is not cream). Finnish
        # "kermajuusto" is a semi-hard cheese, sold by Valio as tilsit; cream cheese is "tuorejuusto".
        (r"tuorejuust\w*", "cream cheese"),
        (r"kermajuust\w*", "tilsit cheese"),
        (r"sulatejuust\w*", "processed cheese"),
        (r"raejuust\w*", "cottage cheese"),
        (r"sinihome-?juust\w*", "blue cheese"),
        (r"vuohen-?juust\w*|vuohi-?juust\w*", "goat cheese"),
        (r"salaatti-?juust\w*", "salad cheese"),
        (r"leipä-?juust\w*", "squeaky cheese"),
        (r"pizza-?juust\w*", "pizza cheese"),
        (r"maitojauh\w*", "milk powder"),
        (r"kermajauh\w*", "cream powder"),
        (r"kaakaojauh\w*", "cocoa powder"),
        (r"leivinjauh\w*", "baking powder"),
        (r"perunamuusijauh\w*", "mashed potato powder"),
        (r"maitorahk\w*", "quark"),
        (r"kasvirasvalevit\w*", "vegetable fat spread"),
        (r"levite|levitettä|levitteen|levitteet|levitteitä|levitteenä", "fat spread"),
        (r"piimä|piimää|piimän|piimät|piimiä", "sour milk"),
        (r"voileip\w*|voileiv\w*", "sandwich"),
        (r"suklaa|suklaata|suklaan", "chocolate"),
        (r"emmental-?juust\w*|emmentaal-?juust\w*", "Emmental cheese"),
        (r"emmental(?:ia|in|ista|iin|ina)?|emmentaal\w*", "Emmental"),
        (r"edam-?juust\w*", "Edam cheese"),
        (r"edam(?:ia|in|ista)?", "Edam"),
        (r"gouda-?juust\w*", "Gouda cheese"),
        (r"gouda(?:a|n|sta)?", "Gouda"),
        (r"cheddar-?juust\w*", "cheddar cheese"),
        (r"cheddar(?:ia|in|ista)?", "cheddar"),
        (r"mozzarella-?juust\w*", "mozzarella cheese"),
        (r"mozzarella(?:a|n|sta)?", "mozzarella"),
        (r"feta-?juust\w*", "feta cheese"),
        (r"feta(?:a|n|sta)?", "feta"),
        (r"halloumi-?juust\w*", "halloumi cheese"),
        (r"halloumi(?:a|n|sta)?", "halloumi"),
        (r"brie-?juust\w*", "brie cheese"),
        (r"brie(?:tä|n)?", "brie"),
        (r"parmesa(?:an|n)\w*", "parmesan"),
        (r"lohifile\w*", "salmon fillet"),
        (r"kirjolo(?:hi|ht|he)\w*", "rainbow trout"),
        (r"lohi|lohta|lohen|lohia|lohet|loheen|lohesta", "salmon"),
        (r"(?:kanan?)?muna(?:a|t|n)?|(?:kanan?)?munia|(?:kanan?)?munien", "eggs"),
        (r"broiler\w*|kana|kanaa|kanan|kanoja|kanafile\w*", "chicken"),
        (r"jauhelih\w*", "minced meat"),
        (r"kala|kalaa|kalan|kaloja", "fish"),
        (r"kinkku\w*|kinkkua|kinkun", "ham"),
        (r"makkar\w*", "sausage"),
        (r"jäätelö\w*", "ice cream"),
        (r"margariin\w*", "margarine"),
        (r"rypsiöljy\w*", "rapeseed oil"),
        (r"öljy\w*", "oil"),
        (r"mehu\w*", "juice"),
        (r"tee|teetä|teen", "tea"),
        (r"kaakao\w*", "cocoa"),
        (r"sokeri\w*", "sugar"),
        (r"suola|suolaa", "salt"),
        (r"sinappi|sinappi(?:a|in|en|na)|sinapi(?:n|t|sta|lla|lle)", "mustard"),
        (r"tomaatti(?:a|in|na)?|tomaat(?:in|it|tia|teja|ista)", "tomato"),
        (r"porkkana(?:a|n|t|na|sta)?|porkkanoi(?:ta|ssa)", "carrot"),
        (r"kurkku(?:a|un|na)?|kurku(?:n|t|sta)|kurkkuja", "cucumber"),
        (r"sipuli(?:a|n|t|in|sta|na|en|lla|lle|ksi)|sipuleita|sipuli", "onion"),
    )
)
# After the product noun of a Finnish request, a word in a place case (illative, adessive, allative,
# inessive, …) starts a destination or a circumstance ("koulukeittiöön yhdellä oppilaalla"), not the product.
_FI_PLACE_CASE_RE = re.compile(
    r"[^\W\d_]{2,}(?:lla|llä|lle|ssa|ssä|lta|ltä|hin|seen|siin|aan|een|iin|oon|uun|yyn|ään|öön|mme|nne)$",
    re.IGNORECASE,
)
# A head count ("for 200 guests", "200 vieraalle") is the occasion, not a quantity or the product.
_HEADCOUNT_RE = re.compile(
    r"\b(?:for\s+)?\d+\s+(?:guests?|people|persons?|pax|diners?|students?|pupils?|children|kids|customers?|"
    r"employees?|covers)\b|\b\d+\s+(?:vieraalle|hengelle|henkilölle|oppilaalle|lapselle|asiakkaalle|"
    r"ruokailijalle|työntekijälle|vierasta|henkeä|henkilöä|oppilasta|lasta|asiakasta|ruokailijaa|"
    r"työntekijää)\b",
    re.IGNORECASE,
)
# Occasion words kept for a clarifying question ("Which product do you need for dessert for 200 guests …?").
_OCCASION_EN_RE = re.compile(
    r"\bfor\s+(?:the\s+|our\s+|a\s+|an\s+)?(?:desserts?|lunch|dinner|breakfast|brunch|supper|snacks?|baking|"
    r"party|event|buffet|banquet|coffee\s+break)\b",
    re.IGNORECASE,
)
_OCCASION_FI_RE = re.compile(
    r"\b(?:jälkiruoaksi|jälkiruuaksi|lounaaksi|päivälliseksi|aamiaiseksi|illalliseksi|välipalaksi|leivontaan|"
    r"juhliin|tapahtumaan|buffetiin|kahvitarjoiluun)\b",
    re.IGNORECASE,
)
# Destinations ("to the school kitchen", "at our canteen") describe the buyer, not the product.
_DESTINATION_RE = re.compile(
    r"\b(?:to|at|into)\s+(?:the|our|my|their|your)\s+[^\W\d_]+(?:\s+(?:kitchen|canteen|cafeteria|restaurant|"
    r"school|office|site|unit|hall))?",
    re.IGNORECASE,
)
# A clock time is a deadline on the delivery day ("klo 14 mennessä", "by 14:00", "before 2 pm").
_TIME_LEAD = r"(?:\b(?:by|before|until|till|no\s+later\s+than|latest\s+at|at)\s+)?"
_TIME_RE = re.compile(
    r"\b(?:klo|kello)\s*(?P<fh>\d{1,2})(?:[:.](?P<fm>\d{2}))?(?:\s+(?:mennessä|asti|saakka))?(?!\w)"
    r"|" + _TIME_LEAD + r"(?<![\d.,:])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap])\.?m\b\.?"
    r"|" + _TIME_LEAD + r"(?<![\d.,:])(?P<h24>\d{1,2}):(?P<m24>\d{2})(?![\d:])",
    re.IGNORECASE,
)
# Greetings are not deadlines ("Good morning, we need …").
_GREETING_RE = re.compile(
    r"\bgood\s+(?:morning|afternoon|evening)\b|\bhyvää\s+(?:huomenta|päivää|iltaa)\b|\bhuomenta\b",
    re.IGNORECASE,
)
# A time of day is a deadline on the delivery day as well (T53): (pattern, phrase shown, latest clock it allows).
_DAYPARTS: tuple[tuple[str, str, str], ...] = (
    (r"(?:early\s+)?(?:in\s+the\s+)?morning|aamulla|aamuksi|aamuun(?:\s+mennessä)?|aamusta",
     "in the morning", "10:00"),
    (r"(?:by|before|at)\s+(?:noon|midday)|noon|midday|aamupäivällä|aamupäiväksi|"
     r"(?:aamupäivään|puoleenpäivään|keskipäivään)(?:\s+mennessä)?", "by noon", "12:00"),
    (r"(?:by|before)\s+lunch(?:time)?|ennen\s+lounasta|lounaaseen\s+mennessä", "before lunch", "11:00"),
    (r"(?:in\s+the\s+)?afternoon|iltapäivällä|iltapäiväksi|iltapäivään(?:\s+mennessä)?",
     "in the afternoon", "16:00"),
    (r"(?:by|before)\s+dinner|ennen\s+päivällistä", "before dinner", "17:00"),
    (r"(?:in\s+the\s+)?evening|tonight|illalla|illaksi|iltaan(?:\s+mennessä)?", "in the evening", "20:00"),
)
_DAYPART_RES = tuple((re.compile(rf"\b(?:{p})\b", re.IGNORECASE), phrase, clock) for p, phrase, clock in _DAYPARTS)
# The occasion or booking behind a request ("unexpected banquet booking") is context, never the product.
_OCCASION_CONTEXT_RE = re.compile(
    r"\b(?:an?\s+)?(?:(?:unexpected|sudden|surprise|last[\s-]minute|extra|big|large|private|corporate|company|"
    r"wedding|birthday|christmas|school|staff)\s+)*(?:banquet|party|event|function|reception|conference|buffet|"
    r"celebration|catering|gala)(?:\s+(?:booking|order|reservation|guests))?\b",
    re.IGNORECASE,
)
# T74: Finnish occasion clauses ("iso juhla illalla", "yrityksen pikkujouluihin") and purpose clauses
# ("juhlia varten") describe the event, not the product. Diet words in them are read before they go.
_OCCASION_CONTEXT_FI_RE = re.compile(
    r"\b(?:(?:iso|isot|isoon|isolle|isoa|suuri|suuret|suureen|suurelle|pieni|pienet|yllättävä|yllättävät|"
    r"ylimääräinen|yksityinen|yrityksen|oma|omat|meidän)\s+)*"
    r"(?:juhl(?!a\s+mokka)\w*|tilaisuu\w*|tapahtum\w*|häät|häihin|hääjuhl\w*|syntymäpäiv\w*|synttär\w*|pikkujoul\w*|"
    r"kokoukse\w*|kokous|seminaar\w*|konferens\w*|pitopalvelu\w*|banketti\w*|illalli\w*|vastaanot\w*|"
    r"catering\w*|noutopöy\w*)"
    r"(?:\s+(?:varten|tilaus|tilaukseen|varaus|varaukseen|vieraille))?\b",
    re.IGNORECASE,
)
_PURPOSE_FI_RE = re.compile(r"\b(?:[^\W\d_]+n\s+)?[^\W\d_]+\s+varten\b", re.IGNORECASE)
# A Finnish word whose usual English reading differs from its literal parts is said in the assumptions.
_READING_NOTES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bkermajuust\w*", re.IGNORECASE),
     "“Kermajuusto” read as tilsit, the semi-hard cheese Valio sells under that name, not cream cheese"),
)
# "or similar" / "tai vastaavaa": an accepted alternative, recorded as an assumption, never part of the item.
_SIMILAR_RE = re.compile(
    r"\bor\s+(?:something\s+)?(?:similar|equivalent|comparable)\b|\bor\s+an?\s+(?:equivalent|alternative)\b|"
    r"\btai\s+(?:muu\s+|muuta\s+|jotain\s+)?vastaava\w*",
    re.IGNORECASE,
)
# The buyer's language decides the language of a clarifying question (fi or en; English when unsure).
_FI_HINT_RE = re.compile(
    r"\b(?:tarvi\w*|halua\w*|tilaa\w*|tilaisi\w*|toimit\w*|lähettä\w*|kiloa|litraa|kappaletta|laatikkoa|"
    r"pakkausta|pussia|purkkia|pulloa|kpl|mennessä|viimeistään|huomenna|tänään|jotain|jotakin|ja|sekä|ei|"
    r"ilman|meille|kiitos|klo|kello|\w{3,}ksi|\w*[äö]\w*)\b",
    re.IGNORECASE,
)
_EN_HINT_RE = re.compile(
    r"\b(?:we|i|need|needs|please|of|the|by|and|for|with|without|no|some|something|today|tomorrow|order|"
    r"send|deliver\w*|our|us)\b",
    re.IGNORECASE,
)
_MILK_RE = re.compile(r"\bmilk\b", re.IGNORECASE)
_PLANT_RE = re.compile(r"\b(?:oat|soy|soya|almond|coconut|rice|pea|plant|vegan|vegetable)\b", re.IGNORECASE)
# "product no. 465039" and the Finnish ordinal date "20. marraskuuta" stay one sentence,
# while "18.11. Do not ask..." (two-part date followed by a capitalised sentence) or "…" splits.
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])(?<!\d\.)\s+(?!\d)|(?:(?<=\.\d\.)|(?<=\.\d\d\.))\s+(?=[A-ZÅÄÖ])|\s*(?:…|\.{2,})\s*|\n+"
)
_CHUNK_SPLIT = re.compile(r",\s+|;\s*|\s+(?:and|ja|sekä|plus|\+|&)\s+", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[()\[\]{}.,:;!?\"“”«»—–/]+")
_ALT_SPLIT_RE = re.compile(r"\s+\b(?:tai|or)\b\s+", re.IGNORECASE)
_ADJECTIVE_FORMS = frozenset({"grated", "sliced", "block", "shredded", "cubed", "whole"})
_KNOWN_EN_PRODUCT_WORDS = frozenset({
    "milk", "drink", "light", "skimmed", "semi-skimmed", "whole", "oat", "soy", "soya", "almond",
    "coconut", "cream", "whipping", "cooking", "coffee", "low-fat", "sour", "cheese", "tilsit",
    "processed", "cottage", "blue", "goat", "salad", "squeaky", "pizza", "emmental", "edam", "gouda",
    "cheddar", "mozzarella", "feta", "halloumi", "brie", "camembert", "parmesan", "fontal", "yoghurt",
    "yogurt", "quark", "butter", "margarine", "vegetable", "fat", "spread", "oil", "rapeseed", "olive",
    "pasta", "macaroni", "spaghetti", "fusilli", "penne", "lasagne", "lasagna", "tagliatelle", "rigatoni",
    "fettuccine", "noodles", "rice", "flour", "wheat", "rye", "barley", "spelt", "flakes", "muesli",
    "granola", "bread", "crispbread", "tea", "green", "black", "cocoa", "chocolate", "sugar", "salt",
    "honey", "syrup", "juice", "apple", "orange", "water", "salmon", "fillet", "rainbow", "trout",
    "flounder", "herring", "cod", "fish", "shrimp", "eggs", "egg", "chicken", "turkey", "meat", "minced",
    "mince", "beef", "pork", "lamb", "reindeer", "ham", "sausage", "bacon", "meatballs", "sauce",
    "bolognese", "soup", "casserole", "steak", "pie", "cake", "ice", "potatoes", "potato", "tomato",
    "tomatoes", "carrot", "carrots", "pea", "peas", "cabbage", "mushroom", "mushrooms", "spinach",
    "liver", "blueberry", "strawberry", "berry", "fresh", "frozen", "smoked", "cold-smoked", "hot-smoked",
    "salted", "unsalted", "grated", "sliced", "slices", "block", "blocks", "shredded", "strips",
    "cubed", "cubes", "diced", "powder", "powdered", "barista", "uht", "esl", "natural",
})

UNIT_WORDS = "litres, kilograms, pieces or cases"

# Explicit exclusion lists keep every term, not just the first allergen after a negation.
# Stop words keep delivery clauses and another exclusion out of the ingredient list.
_EXCLUSION_STOP = "|".join(re.escape(w) for w in sorted(
    _STOPWORDS | _BENIGN_NEGATED | {"no", "without", "ilman", "ei", "eivät"}, key=len, reverse=True,
))
_EXCLUSION_WORD = rf"(?!(?:{_EXCLUSION_STOP})\b)(?:tree[\s-]+nuts?|[^\W\d_][\w-]*)"
_EXCLUSION_JOIN = r"(?:\s*,\s*(?:(?:tai|eikä|ja|sekä|or|and|nor)\s+)?|\s+(?:tai|eikä|ja|sekä|or|and|nor)\s+)"
_EXCLUSION_TERMS = rf"{_EXCLUSION_WORD}(?:{_EXCLUSION_JOIN}{_EXCLUSION_WORD})*"
_EXCLUSION_RES = (
    re.compile(rf"\b(?:(?:ei(?:vät)?\s+saa\s+sisältää|ilman)\s+|{_NEGATION_EN})"
               rf"(?P<terms>{_EXCLUSION_TERMS})", re.IGNORECASE),
    re.compile(rf"\b(?P<terms>{_EXCLUSION_TERMS})\s+ei\s+saa\s+olla\b", re.IGNORECASE),
)
_EXCLUSION_TERM_RE = re.compile(r"tree[\s-]+nuts?|[^\W\d_][\w-]*", re.IGNORECASE)
_EXCLUSION_CONJUNCTIONS = {"tai", "eikä", "ja", "sekä", "or", "and", "nor"}
_UNKNOWN_FI_EXCLUSION_RE = re.compile(r"\bei(?:vät)?\s+saa\s+sisältää\b|\bei\s+saa\s+olla\b", re.IGNORECASE)
# Only a standalone free-from statement is read this way, never a suffix inside a brand/product name.
_FI_FREE_STATEMENT_RE = re.compile(
    r"(?:täysin\s+)?[^\W\d_]+(?:ton|tön|tonta|töntä|ttomia|ttömiä|ttoman|ttömän)", re.IGNORECASE,
)


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
                elif kind in ("dmy", "dmon", "mond", "dfi"):
                    if kind == "dmy":
                        month = int(m["m"])
                    elif kind == "dfi":
                        month = _MONTHS_FI.index(m["fimon"].lower()) + 1
                    else:
                        month = _MONTHS.index(m["mon"].lower()[:3]) + 1
                    explicit_year = kind in ("dmy", "dfi") and bool(m["y"])
                    year = int(m["y"]) if explicit_year else today.year
                    value = date(year, month, int(m["d"]))
                    if not explicit_year and value < today:
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


def _is_finnish(text: str) -> bool:
    return len(_FI_HINT_RE.findall(text)) > len(_EN_HINT_RE.findall(text))


@dataclass(frozen=True)
class _Deadline:
    phrase: str  # as shown: "by 14:00", "in the morning"
    clock: str  # the latest "HH:MM" it allows, to read a deadline without a date as today or tomorrow


def _take_deadline(text: str) -> tuple[_Deadline | None, str]:
    """The first deadline (a clock time, else a time of day) and the text with every such phrase cut out."""
    found: list[_Deadline] = []

    def take(m: re.Match) -> str:
        hour = int(m["fh"] or m["h"] or m["h24"])
        minute = int(m["fm"] or m["m"] or m["m24"] or 0)
        if m["ap"]:
            if not 1 <= hour <= 12:
                return m.group(0)
            hour = hour % 12 + (12 if m["ap"].lower() == "p" else 0)
        if hour > 23 or minute > 59:
            return m.group(0)
        clock = f"{hour:02d}:{minute:02d}"
        found.append(_Deadline(f"by {clock}", clock))
        return " "

    rest = _TIME_RE.sub(take, _GREETING_RE.sub(" ", text))
    for pattern, phrase, clock in _DAYPART_RES:
        if pattern.search(rest):
            found.append(_Deadline(phrase, clock))
            rest = pattern.sub(" ", rest)
    # Clock times are found first, so an exact time wins over a time of day ("tomorrow morning by 9:00").
    return (found[0] if found else None), rest


# Finnish compounds take their meaning from the last part (T53): "jauhelihakastike" is a meat *sauce*, not
# mince. (head pattern at the end of the word, English head, needs a modifier in front). Explicit inflected
# endings keep "salaattijuusto" (a cheese) from reading as a salad.
_COMPOUND_HEADS: tuple[tuple[re.Pattern, str, bool], ...] = tuple(
    (re.compile(pattern + "$"), english, needs_modifier) for pattern, english, needs_modifier in (
        (r"kastik(?:e|etta|keen|keet|keita|keeseen|keena)", "sauce", False),
        (r"keit(?:to|toa|on|ot|toja|toon)", "soup", False),
        (r"pull(?:a|aa|an|at|ia|ien|iin)", "balls", True),
        (r"laatik(?:ko|koa|on|ot|oita|koon)", "casserole", True),
        (r"pihv(?:i|iä|in|it|ejä|eihin)", "steak", False),
        (r"salaat(?:ti|tia|in|it|teja)", "salad", False),
        (r"piira(?:kka|kkaa|kan|kat|koita|s)", "pie", True),
        (r"kak(?:ku|kua|un|ut|kuja)", "cake", True),
    )
)
_COMPOUND_MODIFIERS = {
    "liha": "meat", "jauheliha": "meat", "kala": "fish", "lohi": "salmon", "juusto": "cheese", "kerma": "cream",
    "kana": "chicken", "broileri": "chicken", "sieni": "mushroom", "tomaatti": "tomato", "peruna": "potato",
    "kasvis": "vegetable", "makaroni": "macaroni", "porkkana": "carrot", "herne": "pea", "kaali": "cabbage",
    "nauta": "beef", "naudan": "beef", "porsaan": "pork", "possu": "pork", "kanan": "chicken", "riisi": "rice",
    "mansikka": "strawberry", "suklaa": "chocolate", "omena": "apple", "marja": "berry", "mustikka": "blueberry",
    "vihannes": "vegetable", "kinkku": "ham", "pinaatti": "spinach", "maksa": "liver", "ruis": "rye",
    "emmental": "Emmental", "emmentaal": "Emmental", "edam": "Edam", "gouda": "Gouda", "cheddar": "cheddar",
    "mozzarella": "mozzarella", "feta": "feta", "halloumi": "halloumi", "brie": "brie", "parmesan": "parmesan",
    "parmesaani": "parmesan", "sinihome": "blue", "vuohi": "goat", "vuohen": "goat", "salaatti": "salad",
    "kaura": "oat", "soija": "soy", "manteli": "almond", "kookos": "coconut", "vehnä": "wheat", "ohra": "barley",
    "speltti": "spelt", "kirjolohi": "rainbow trout", "kalkkuna": "turkey", "lammas": "lamb", "karitsa": "lamb",
    "karitsan": "lamb", "poro": "reindeer", "poron": "reindeer",
}
_COMPOUND_SPECIAL = {"meat balls": "meatballs", "fish balls": "fish balls", "chicken balls": "chicken balls"}


def _compound(lower: str) -> str | None:
    """"jauhelihakastiketta" -> "meat sauce", "kalakeittoa" -> "fish soup", "lihapullia" -> "meatballs"."""
    for pattern, head, needs_modifier in _COMPOUND_HEADS:
        m = pattern.search(lower)
        if m is None:
            continue
        prefix = lower[: m.start()].rstrip("-")
        if not prefix:
            return None if needs_modifier else head
        modifier = _COMPOUND_MODIFIERS.get(prefix)
        if modifier is None and len(prefix) >= 3:
            # A modifier the table does not know is kept as written ("sienikastike" -> "sieni sauce").
            modifier = prefix
        if modifier is None:
            return None
        english = f"{modifier} {head}"
        return _COMPOUND_SPECIAL.get(english, english)
    return None


def _modifier_part(part: str) -> str | None:
    """English for the first part of a Finnish compound, in its basic form ("kerma", "maito", "riisi")."""
    if part in _COMPOUND_MODIFIERS:
        return _COMPOUND_MODIFIERS[part]
    for pattern, english in _FINNISH_FORMS:
        if pattern.fullmatch(part):
            return english
    for stem, english in _FINNISH_NOUNS:
        if part.startswith(stem) and len(part) - len(stem) <= 1:
            return english
    return None


def _head_part(part: str) -> str | None:
    """English for the last part of a Finnish compound, in any inflection ("juustoa", "jauhoja")."""
    for pattern, english in _FINNISH_FORMS:
        if pattern.fullmatch(part):
            return english
    for stem, english in _FINNISH_NOUNS:
        if part.startswith(stem):
            return english
    return None


def _head_compound(lower: str) -> str | None:
    """T74: a Finnish compound takes its type from its last part, so the longest reading wins over the first
    word: "riisijauhoa" is rice *flour* (not rice), "maitosuklaata" milk chocolate, "kermajäätelö" ice cream.
    Both parts must be known words, otherwise the word is left to the stems and kept as written."""
    for cut in range(3, len(lower) - 2):
        head = _head_part(lower[cut:].lstrip("-"))
        if head is None:
            continue
        modifier = _modifier_part(lower[:cut].rstrip("-"))
        if modifier is None:
            continue
        if modifier in head:
            return head
        if head in _ADJECTIVE_FORMS:
            return f"{head} {modifier}"
        return f"{modifier} {head}"
    return None


def _translate(token: str) -> tuple[str, bool]:
    """Finnish product noun (any inflection) -> (English, True); anything else (brands, names) as written.

    Whole-word inflections come first, then compounds by their last part, then the parser's core stems,
    then the catalogue's FI/SV stem table (T04), so the request and the search speak the same vocabulary.
    """
    lower = token.lower()
    if compound := _compound(lower):
        return compound, True
    for pattern, english in _FINNISH_FORMS:
        if pattern.fullmatch(lower):
            return english, True
    if head := _head_compound(lower):
        return head, True
    for stem, english in _FINNISH_NOUNS:
        if lower.startswith(stem):
            return english, True
    for stem, english_terms in FI_SV_TO_EN_SYNONYMS:
        if len(stem) >= 4 and lower.startswith(stem):
            return english_terms[0], True
    if "-" in token and not any(token.lower() == b.lower() for b in KNOWN_BRANDS):
        parts = [p for p in token.split("-") if p]
        if len(parts) >= 2:
            sub = [_translate(p) for p in parts]
            if any(k for _, k in sub):
                words = [eng for eng, _ in sub]
                if len(words) == 2 and words[1] in _ADJECTIVE_FORMS:
                    words = [words[1], words[0]]
                return " ".join(words), True
    if lower in _KNOWN_EN_PRODUCT_WORDS:
        return token, True
    return token, False


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
    for pattern, replacement in _CREAM_PURPOSE_RES:
        rest = pattern.sub(replacement, rest)
    rest, excluded, _unknown = _take_exclusions(rest)
    requirements.extend(excluded)
    for pattern, _found in _DIET_RES:
        rest = pattern.sub(" ", rest)
    for pattern, found in _REQUIREMENT_RES:
        if pattern.search(rest):
            requirements.extend(r for r in found if r not in requirements)
            rest = pattern.sub(" ", rest)
    for pattern, preference in _PREFERENCES:
        if pattern.search(rest):
            preferences.append(preference)
            rest = pattern.sub(" ", rest)
    for pattern, code, _label in _CERTIFICATIONS:
        if pattern.search(rest):
            preferences.append(Preference(kind="LABEL", value=code))
            rest = pattern.sub(" ", rest)

    def take_fat(m: re.Match) -> str:
        preferences.append(Preference(kind="FAT_PERCENT", value=m.group(1).replace(",", ".")))
        return " "

    rest = _FAT_RE.sub(take_fat, rest)

    def take_pack_phrase(m: re.Match) -> str:
        unit = next(name for name, _pattern in _PACK_UNIT_GROUPS if m.group(f"pu_{name}"))
        packs.append(f"{_plain(_to_decimal(m['num']))} {unit}")
        return " "

    rest = _PACK_PHRASE_RE.sub(take_pack_phrase, rest)

    def take_pack(m: re.Match) -> str:
        packs.append(f"{_plain(_to_decimal(m['num']))} {_unit_of(m)}")
        return " "

    rest = _QUANTITY_RE.sub(take_pack, rest)
    for _kind, pattern in _DATE_RES:
        rest = pattern.sub(" ", rest)
    rest = _HEADCOUNT_RE.sub(" ", rest)
    rest = _OCCASION_CONTEXT_RE.sub(" ", rest)
    rest = _OCCASION_CONTEXT_FI_RE.sub(" ", rest)
    rest = _PURPOSE_RE.sub(" ", rest)
    rest = _PURPOSE_FI_RE.sub(" ", rest)
    rest = _DESTINATION_RE.sub(" ", rest)
    rest = _BENIGN_NEGATION_RE.sub(" ", rest)
    if req_form := extract_requested_form(rest):
        form_pref = Preference(kind="OTHER", value=req_form.label)
        if form_pref not in preferences:
            preferences.append(form_pref)
    return rest, requirements, preferences, product_id, packs


def _order_quantities(text: str) -> list[re.Match]:
    """Pack sizes describe an item, not how much the buyer wants to order."""
    packs = [match.span() for match in _PACK_PHRASE_RE.finditer(text)]
    return [match for match in _QUANTITY_RE.finditer(text)
            if not any(start <= match.start() < end for start, end in packs)]


def _form_only(what: str | None) -> bool:
    return bool(what) and all(word.lower() in {
        "grated", "sliced", "slices", "block", "blocks", "shredded", "strips", "cubed", "cubes",
        "diced", "whole", "powder", "powdered",
    } for word in what.split())


def _item_name(before: str, after: str, finnish: bool) -> str | None:
    leading, trailing = _what(_strip_lead(before), finnish), _what(after, finnish)
    if _form_only(trailing) and leading and not _form_only(leading):
        return f"{trailing} {leading}"
    if _form_only(leading) and trailing and not _form_only(trailing):
        return f"{leading} {trailing}"
    name = trailing or leading
    return None if _form_only(name) or (name and _BARE_CONTAINER_RE.fullmatch(name)) else name


def _take_exclusions(text: str) -> tuple[str, list[Requirement], str | None]:
    """Read explicit ingredient lists with the same vocabulary as individual free-from phrases."""
    requirements: list[Requirement] = []
    unknown = None
    brands: list[tuple[int, int]] = []

    def take(match: re.Match) -> str:
        nonlocal unknown
        # "No" inside a brand or product name ("Oatly No Sugars") is the name, not an exclusion (T74).
        if any(start <= match.start() < end for start, end in brands):
            return match.group(0)
        for term in _EXCLUSION_TERM_RE.finditer(match["terms"]):
            word = term.group(0)
            if word.lower() in _EXCLUSION_CONJUNCTIONS:
                continue
            # A delivery date after a comma ends the list, rather than naming an ingredient.
            if any(pattern.fullmatch(word) for _kind, pattern in _DATE_RES):
                return " " + match.group(0)[term.start() + match.start("terms") - match.start():]
            found = next((reqs for reqs, en, fi in _ALLERGEN_VOCAB
                          if (en and re.fullmatch(en, word, re.IGNORECASE))
                          or (fi and re.fullmatch(rf"(?:{fi})\w*", word, re.IGNORECASE))), None)
            if found is None:
                unknown = unknown or " ".join(match.group(0).split())
            else:
                requirements.extend(r for r in found if r not in requirements)
        return " "

    for pattern in _EXCLUSION_RES:
        brands = [m.span() for m in _BRAND_PHRASE_RE.finditer(text)]
        text = pattern.sub(take, text)
    return text, requirements, unknown


def _unknown_negation(text: str) -> str | None:
    """Return a negated word the vocabulary cannot map ("no sugar", "salt-free"), or None.

    Words inside a brand or product name ("Oatly No Sugars") are part of the name, not a negation (T74)."""
    rest = _BRAND_PHRASE_RE.sub(" ", text)
    rest = _PRODUCT_ID_RE.sub(" ", rest)
    rest, _requirements, unknown = _take_exclusions(rest)
    if unknown:
        return unknown
    for pattern, _found in _REQUIREMENT_RES:
        rest = pattern.sub(" ", rest)
    for _kind, pattern in _DATE_RES:
        rest = pattern.sub(" ", rest)
    if match := _UNKNOWN_FI_EXCLUSION_RE.search(rest):
        return match.group(0)
    for sentence in _SENTENCE_SPLIT.split(rest):
        for chunk in _split_chunks(sentence):
            statement = chunk.strip(" .!?;:\t\n")
            if _FI_FREE_STATEMENT_RE.fullmatch(statement):
                return statement
    for m in _UNKNOWN_NEGATION_RE.finditer(rest):
        word = (m["word"] or m["free"]).lower()
        if word not in _BENIGN_NEGATED:
            return " ".join(m.group(0).split())
    return None


def _words_single(rest: str, finnish: bool = False) -> tuple[str | None, list[str]]:
    raw = [t for t in _PUNCTUATION.sub(" ", rest).split()
           if t.lower().strip("'’") not in _STOPWORDS and not re.fullmatch(r"[-®™'’]+", t)]
    tokens = [(t, *_translate(t)) for t in raw]
    # A product noun translated from Finnish makes the item Finnish even when the request has no other
    # Finnish hint ("40 kg juustoa hotellin aamiaiselle"), so its destination tail goes too (T74, T87).
    has_finnish_translation = any(known and original.lower() != english.lower() for original, english, known in tokens)
    if finnish or has_finnish_translation:
        tokens = _cut_finnish_tail(tokens)
    words: list[str] = []
    for _original, english, _known in tokens:
        if not words or words[-1] != english:
            words.append(english)
    if len(words) >= 2 and words[-1] in _ADJECTIVE_FORMS and words[0] not in _ADJECTIVE_FORMS:
        words = [words[-1], *words[:-1]]
    return " ".join(words).strip(" -") or None, [original for original, _e, _k in tokens]


def _words(rest: str, finnish: bool = False) -> tuple[str | None, list[str]]:
    """The item name in English, plus the buyer's own words for it (for a question in their language)."""
    if _ALT_SPLIT_RE.search(rest):
        parts = [p for p in _ALT_SPLIT_RE.split(rest) if p and p.strip()]
        if len(parts) >= 2:
            parsed = [_words_single(p, finnish) for p in parts]
            non_empty = [(w, origs) for w, origs in parsed if w]
            if len(non_empty) >= 2:
                joined_what = " or ".join(w for w, _ in non_empty)
                joiner = " tai " if finnish else " or "
                joined_orig: list[str] = []
                for idx, (_w, origs) in enumerate(non_empty):
                    if idx > 0:
                        joined_orig.append(joiner.strip())
                    joined_orig.extend(origs)
                return joined_what, joined_orig
    return _words_single(rest, finnish)


def _is_genitive_word(token: tuple[str, str, bool]) -> bool:
    original, _english, known = token
    return not known and original[:1].islower() and len(original) >= 4 and original.lower().endswith("n")


def _cut_finnish_tail(tokens: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    """Drop destination, circumstance and untranslated Finnish leftover words from a Finnish item (T74, T87)."""

    def place(original: str, known: bool) -> bool:
        return not known and original[:1].islower() and bool(_FI_PLACE_CASE_RE.fullmatch(original))

    known_at = [n for n, (_o, _e, known) in enumerate(tokens) if known]
    if known_at:
        # After the product noun, the first place-case word starts the tail: everything from there goes,
        # with the genitives that lead into it ("koulun lounaalle": for the school's lunch).
        for n in range(known_at[-1] + 1, len(tokens)):
            if place(tokens[n][0], tokens[n][2]):
                while n - 1 > known_at[-1] and _is_genitive_word(tokens[n - 1]):
                    n -= 1
                tokens = tokens[:n]
                break
        else:
            # A genitive left dangling at the end modified something already removed ("… koulun [lounaalle]").
            end = len(tokens)
            while end - 1 > known_at[-1] and _is_genitive_word(tokens[end - 1]):
                end -= 1
            tokens = tokens[:end]
        # T87: when known product words exist, drop any remaining untranslated lowercase Finnish fragments
        # (keeping capitalised brand/product-line words).
        kept_known = [t for t in tokens if t[2] or not t[0][:1].islower()]
        if kept_known:
            return kept_known
        return tokens
    kept = [t for t in tokens if not place(t[0], t[2])]
    return kept or tokens


def _what(rest: str, finnish: bool = False) -> str | None:
    return _words(rest, finnish)[0]


_INSTRUCTION_BRIDGE_RE = re.compile(
    r"^(?:[\s,;]*\b(?:so|and|or|then|please|pls|therefore|thus|because|as|since|just|also|"
    r"ja|sekä|joten|koska|eli|tai|että|niin|it|this|that)\b[\s,;]*|[\s,;]+)+$",
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;,\n…]|\.{2,}|—|–")
_INSTRUCTION_LEAD_ONLY_RE = re.compile(
    r"^(?:[\s,;]*\b(?:this|the|our|order|request|is|was|has|have|been|so|and|because|as|since|then|"
    r"please|pls|ceo|cfo|coo|manager|purchasing|procurement|head|chef|boss|director|approver|admin|"
    r"tilaus|tämä|on|jo|joten|koska|sekä|ja)\b[\s,;]*)+$",
    re.IGNORECASE,
)


def _has_order_content(span_text: str) -> bool:
    if _QUANTITY_RE.search(span_text) or _NUMERAL_RUN.search(span_text):
        return True
    if any(p.search(span_text) for _k, p in _DATE_RES):
        return True
    if any(p.search(span_text) for p, _r in _REQUIREMENT_RES) or any(p.search(span_text) for p, _r in _DIET_RES):
        return True
    _, excl, _ = _take_exclusions(span_text)
    return bool(excl)


def _cut_instruction_clauses(text: str, ignored: list[IgnoredInstruction]) -> str:
    """Cut every instruction span (and its surrounding non-item clause) out of `text` before item extraction (T87)."""
    if not ignored:
        return text
    spans: list[list[int]] = [[f.start, f.end] for f in sorted(ignored, key=lambda f: f.start)]
    merged: list[list[int]] = [spans[0]]
    for s, e in spans[1:]:
        prev_s, prev_e = merged[-1]
        between = text[prev_e:s]
        if _INSTRUCTION_BRIDGE_RE.fullmatch(between) and not _has_order_content(between):
            merged[-1][1] = e
        else:
            merged.append([s, e])

    boundaries = [0, *(m.start() for m in _CLAUSE_BOUNDARY_RE.finditer(text)), *(m.end() for m in _CLAUSE_BOUNDARY_RE.finditer(text)), len(text)]
    boundaries = sorted(set(boundaries))

    expanded: list[tuple[int, int]] = []
    for s, e in merged:
        clause_start = max((b for b in boundaries if b <= s), default=0)
        clause_end = min((b for b in boundaries if b >= e), default=len(text))
        tail = text[e:clause_end]
        if tail and not _has_order_content(tail):
            e = clause_end
        lead = text[clause_start:s]
        if lead and not _has_order_content(lead) and _INSTRUCTION_LEAD_ONLY_RE.fullmatch(lead):
            s = clause_start
        expanded.append((s, e))

    clean = text
    for s, e in reversed(expanded):
        clean = clean[:s] + ", " + clean[e:]
    return clean


_LIQUID_CONCEPT_IDS = frozenset({
    "oat_drink", "soy_drink", "almond_drink", "coconut_milk", "milk", "cream", "oil", "juice",
})
_PIECE_CONCEPT_IDS = frozenset({"Disposable tableware", "cleaning_waste"})
_LIQUID_KEYWORDS_RE = re.compile(r"\b(?:drink|milk|cream|juice|oil|water|lemonade|sour\s+milk|buttermilk)\b", re.IGNORECASE)
_SOLID_KEYWORDS_RE = re.compile(
    r"\b(?:cheese|butter|margarine|spread|yoghurt|yogurt|quark|pasta|macaroni|spaghetti|fusilli|penne|"
    r"lasagne|noodles|rice|flour|flakes|muesli|bread|crispbread|coffee|tea|sugar|salt|salmon|trout|"
    r"flounder|herring|cod|fish|shrimp|chicken|turkey|meat|mince|minced|beef|pork|lamb|reindeer|ham|"
    r"sausage|meatballs|sauce|bolognese|casserole|steak|pie|cake|potatoes|potato|eggs)\b",
    re.IGNORECASE,
)


def _natural_unit_for_alternative(alt: str) -> str | None:
    concepts = resolve_query_concepts(alt)
    if concepts:
        cid = concepts[0].concept_id
        if cid in _LIQUID_CONCEPT_IDS:
            return "l"
        if cid in _PIECE_CONCEPT_IDS:
            return "pcs"
        return "kg"
    if _LIQUID_KEYWORDS_RE.search(alt):
        return "l"
    if _SOLID_KEYWORDS_RE.search(alt):
        return "kg"
    return None


def _alternative_unit_conflict(what: str, finnish: bool) -> str | None:
    """When 'X or Y' / 'X tai Y' combines products that need different units (e.g. milk in l vs cheese in kg),
    ask a clarifying question (T87 Scope #3)."""
    alts = split_query_alternatives(what)
    if len(alts) < 2:
        return None
    units = [(alt, _natural_unit_for_alternative(alt)) for alt in alts]
    known = [(alt, u) for alt, u in units if u is not None]
    if len({u for _, u in known}) < 2:
        return None
    first_alt, first_u = known[0]
    second_alt, second_u = next((alt, u) for alt, u in known[1:] if u != first_u)
    unit_en = {"l": "litres", "kg": "kilograms", "pcs": "pieces"}
    unit_fi = {"l": "litroina", "kg": "kiloina", "pcs": "kappaleina"}
    if finnish:
        return (
            f"Vaihtoehdot tilataan eri yksiköissä ({first_alt} {unit_fi.get(first_u, first_u)}, "
            f"{second_alt} {unit_fi.get(second_u, second_u)}): kumpaa tuotetta ja kuinka paljon tarvitsette?"
        )
    return (
        f"{first_alt[:1].upper()}{first_alt[1:]} is ordered in {unit_en.get(first_u, first_u)} and "
        f"{second_alt} in {unit_en.get(second_u, second_u)}: which product and how much do you need?"
    )


@dataclass
class _Fallback:
    request: StructuredRequest | None
    question: str | None
    safety_question: str | None  # unsupported unit or contradiction: wins over any LLM result
    explicit_date: date | None
    items: list[_ItemDraft]
    notes: list[str] = field(default_factory=list)  # request-level facts any reading must keep (deadline)
    global_requirements: list[Requirement] = field(default_factory=list)


def _split_chunks(sentence: str) -> list[str]:
    return [c for c in _CHUNK_SPLIT.split(sentence) if c and c.strip()]


# --------------------------------------------------------------------------- questions (buyer's language)

UNIT_WORDS_FI = "litroina, kiloina, kappaleina tai laatikkoina"


def _how_much_question(what: str, originals: list[str], finnish: bool) -> str:
    if not finnish:
        return f"How much {what} do you need, in {UNIT_WORDS}?"
    # "Kuinka paljon voita tarvitsette": the buyer's own partitive words read naturally; anything else
    # (a nominative, a brand on its own) would not, so the question then leaves the product out.
    if originals and originals[-1].lower().endswith(("a", "ä")):
        return f"Kuinka paljon {' '.join(originals)} tarvitsette ({UNIT_WORDS_FI})?"
    return f"Kuinka paljon tarvitsette ({UNIT_WORDS_FI})?"


def _what_question(clean: str, finnish: bool, dated: bool) -> str:
    occasion = _OCCASION_FI_RE if finnish else _OCCASION_EN_RE
    spans = [m for m in _HEADCOUNT_RE.finditer(clean) if m.group(0).lower().endswith("lle") == finnish]
    spans = sorted([*occasion.finditer(clean), *spans], key=lambda m: m.start())
    parts = [" ".join(m.group(0).split()) for m in spans]
    if not finnish:
        parts = [p if p.lower().startswith("for ") else f"for {p}" for p in parts]
    context = " ".join(parts)
    if finnish:
        if context:
            return (f"Mitä tuotetta ja kuinka paljon tarvitsette {context} ({UNIT_WORDS_FI})?" if dated
                    else f"Mitä tuotetta, kuinka paljon ja mihin mennessä tarvitsette {context}?")
        return (f"Mitä tarvitsette ja kuinka paljon ({UNIT_WORDS_FI})?" if dated
                else "Mitä tarvitsette, kuinka paljon ja mihin mennessä?")
    if context:
        return (f"Which product do you need {context}, and how much ({UNIT_WORDS})?" if dated
                else f"Which product do you need {context}, how much, and by when?")
    return f"What do you need, and how much ({UNIT_WORDS})?" if dated else "What do you need, how much, and by when?"


def _portions_question(what: str | None, portions: Decimal, finnish: bool) -> str:
    if finnish:
        return (f"Annos ei ole tilausyksikkö: kuinka paljon tarvitsette {_plain(portions)} annokseen "
                f"({UNIT_WORDS_FI})?")
    return (f"Portions are not an order unit: how many {UNIT_WORDS} of {what or 'the product'} "
            f"do you need for {_plain(portions)} portions?")


def _negation_question(phrase: str, finnish: bool) -> str:
    if finnish:
        return f"Mitä ”{phrase}” tarkoittaa tässä tilauksessa: mitä ainesosaa tai allergeenia tuotteet eivät saa sisältää?"
    return f"What does “{phrase}” mean for this order: which ingredient or allergen must the products not contain?"


def _which_product_question(raw_quantity: str, finnish: bool) -> str:
    return f"Mitä tuotetta tarvitsette {raw_quantity}?" if finnish else f"Which product do you need {raw_quantity} of?"


def _deadline_note(deadline: _Deadline, requested: date, today: date) -> str:
    day = f"today ({format_date(requested)})" if requested == today else f"on {format_date(requested)}"
    return f"Needed {day} {deadline.phrase}: a later delivery does not meet this request"


def _fallback(
    text: str,
    today: date,
    ignored: list[IgnoredInstruction],
    finnish: bool = False,
    clock: str | None = None,
) -> _Fallback:
    """Deterministic reading. `clock` is the buyer's local time "HH:MM" (for "by 14:00" without a date)."""
    # Request text is data: instruction-like spans and clauses are cut out before anything else reads the text (T87).
    clean = _cut_instruction_clauses(text, ignored)
    clean = _normalise_words(clean)
    deadline, clean = _take_deadline(clean)

    dates = _find_dates(clean, today)
    explicit = dates[0] if dates else None
    deadline_day: date | None = None
    if deadline is not None:
        # A clock time without a date means today while that time is still ahead, otherwise tomorrow.
        ahead = clock is None or clock < deadline.clock
        deadline_day = explicit.value if explicit else (today if ahead else today + timedelta(days=1))

    items: list[_ItemDraft] = []
    extra_requirements: list[Requirement] = []
    for pattern, found in _DIET_RES:
        if pattern.search(clean):
            extra_requirements.extend(r for r in found if r not in extra_requirements)
    for sentence in _SENTENCE_SPLIT.split(clean):
        if items and _PACK_PHRASE_RE.search(sentence) and not _order_quantities(sentence):
            rest, requirements, preferences, _product_id, packs = _extract(sentence)
            if _what(rest, finnish) is None:
                additions = [*preferences, *(Preference(kind="PACK_SIZE", value=pack) for pack in packs)]
                items[-1].preferences.extend(pref for pref in additions if pref not in items[-1].preferences)
                items[-1].requirements.extend(req for req in requirements if req not in items[-1].requirements)
                continue
        if not _QUANTITY_RE.search(sentence):
            # A sentence without a quantity can still carry a hard requirement ("It must be gluten-free").
            for pattern, found in _REQUIREMENT_RES:
                if pattern.search(sentence):
                    extra_requirements.extend(r for r in found if r not in extra_requirements)
            _rest, found, _unknown = _take_exclusions(sentence)
            extra_requirements.extend(r for r in found if r not in extra_requirements)
            continue
        fragments: list[str] = []
        pending = ""
        for chunk in _split_chunks(sentence):
            if _order_quantities(chunk):
                fragments.append(f"{pending} {chunk}".strip())
                pending = ""
            elif fragments:
                fragments[-1] = f"{fragments[-1]} {chunk}"
            else:
                pending = f"{pending} {chunk}".strip()
        for fragment in fragments:
            similar = bool(_SIMILAR_RE.search(fragment))
            fragment = _SIMILAR_RE.sub(" ", fragment)
            first = _order_quantities(fragment)[0]
            before, after = fragment[: first.start()], fragment[first.end() :]
            rest_after, req_a, pref_a, id_a, packs = _extract(after)
            rest_before, req_b, pref_b, id_b, leading_packs = _extract(before)
            requirements = req_b + [r for r in req_a if r not in req_b]
            what = _item_name(rest_before, rest_after, finnish)
            unit = _unit_of(first)
            value = _to_decimal(first["num"])
            notes: list[str] = []
            if unit in ("g", "ml", "dl"):
                factor = {"g": Decimal(1000), "ml": Decimal(1000), "dl": Decimal(10)}[unit]
                converted = value / factor
                target = "kg" if unit == "g" else "l"
                notes.append(f"{_plain(value)} {unit} read as {_plain(converted)} {target}")
                value, unit = converted, target
            preferences = list(dict.fromkeys([
                *pref_b, *pref_a, *(Preference(kind="PACK_SIZE", value=p) for p in [*leading_packs, *packs]),
            ]))
            if similar:
                notes.append(f"Similar products accepted for {what or 'this item'}")
            items.append(_ItemDraft(value, unit, first.group(0), what, id_a or id_b, requirements, preferences, notes))

    for item in items:
        for requirement in extra_requirements:
            if requirement not in item.requirements:
                item.requirements.append(requirement)

    # O6: exactly one question, the most important first.
    weekday_pattern = next(pattern for kind, pattern in _DATE_RES if kind == "weekday")
    inconsistent_weekday = any(
        match["wd_day"] and _next_weekday(today, _WEEKDAYS_EN.index(match["wd"].lower())).day != int(match["wd_day"])
        for match in weekday_pattern.finditer(clean)
    )
    safety_question = (
        "Viikonpäivä ja päivämäärä eivät täsmää: minkä toimituspäivän tarkoitatte?" if finnish
        else "The weekday and day number do not match: which delivery date do you mean?"
    ) if inconsistent_weekday else None
    if not items and _form_only(_what(_extract(clean)[0], finnish)):
        safety_question = _what_question(clean, finnish, dated=explicit is not None)
    question = None
    if not items:
        rest, found_requirements, *_ = _extract(clean)
        stripped = _strip_lead(rest)
        # Name the product only when the text reads as a purchase ("we need …", "gluten-free …").
        what, originals = _words(stripped, finnish) if (stripped != rest or found_requirements) else (None, [])
        question = (
            _how_much_question(what, originals, finnish) if what
            else _what_question(clean, finnish, dated=explicit is not None or deadline is not None)
        )
        if safety_question is None and _HEDGE_RE.search(_SIMILAR_RE.sub(" ", clean)):
            safety_question = question
    for item in items:
        if item.unit == "portions":
            safety_question = _portions_question(item.what, item.quantity, finnish)
            break
    if safety_question is None and (phrase := _unknown_negation(clean)):
        safety_question = _negation_question(phrase, finnish)
    if safety_question is None:
        for item in items:
            if item.what and (contradiction := _contradiction(item.what, item.requirements, finnish)):
                safety_question = contradiction
                break
    if safety_question is None:
        for item in items:
            if item.what and (unit_conflict := _alternative_unit_conflict(item.what, finnish)):
                safety_question = unit_conflict
                break
    if safety_question is None:
        for item in items:
            if item.what is None:
                safety_question = _which_product_question(item.raw_quantity, finnish)
                break
    if safety_question is None and items and _HEDGE_RE.search(_SIMILAR_RE.sub(" ", clean)):
        described = ", ".join(f"{item.what or 'the product'} ({item.raw_quantity})" for item in items)
        safety_question = (
            f"Voitteko vahvistaa täsmälliset tuotteet ja määrät ennen tilausta: {described}?" if finnish
            else f"Could you confirm the exact products and quantities before ordering: {described}?"
        )
    if safety_question is None and not items and _PACK_PHRASE_RE.search(clean):
        safety_question = (
            "Pakkauskoko ei kerro tilausmäärää: kuinka monta pakkausta tai kuinka paljon tuotetta tarvitsette?" if finnish
            else "The pack size does not specify an order quantity: how many packs or how much product do you need?"
        )
    if question is None and safety_question is None:
        for item in items:
            if not item.what:
                question = _which_product_question(item.raw_quantity, finnish)
                break

    notes: list[str] = []
    requested = deadline_day or (explicit.value if explicit else today + timedelta(days=1))
    if deadline is not None:
        notes.append(_deadline_note(deadline, requested, today))
    if extra_requirements:
        labels = ", ".join(_REQUIREMENT_LABELS.get(r.code, r.code) for r in extra_requirements)
        notes.append(f"Applied to every item: {labels}")
    notes.extend(note for pattern, note in _READING_NOTES if items and pattern.search(clean))
    notes.extend(_label_notes([p for item in items for p in item.preferences]))
    request = None
    if items and question is None and safety_question is None:
        assumptions: list[str] = []
        if explicit is None and deadline is None:
            assumptions.append(f"No delivery date given: assumed {format_date(requested)} (tomorrow)")
        elif explicit is None:
            day = "today" if requested == today else "tomorrow"
            assumptions.append(
                f"No delivery date given: “{deadline.phrase}” read as {format_date(requested)} ({day})"
            )
        elif explicit.kind in ("weekday", "weekday_fi"):
            assumptions.append(f"Delivered on or before {format_date(requested)}")
        assumptions.extend(notes)
        for item in items:
            assumptions.extend(item.notes)
            assumptions.extend(_item_assumptions(item.what or "", item.preferences))
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
    explicit_date = deadline_day or (explicit.value if explicit else None)
    return _Fallback(request, question, safety_question, explicit_date, items, notes, extra_requirements)


# Number words (EN/FI) directly before a unit: "ten cases", "kaksikymmentä litraa", "tuhat litraa".
_EN_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_FI_UNITS = {
    "yksi": 1, "kaksi": 2, "kolme": 3, "neljä": 4, "viisi": 5, "kuusi": 6, "seitsemän": 7, "kahdeksan": 8,
    "yhdeksän": 9,
}
_FI_MORPHEMES = sorted([*_FI_UNITS, "kymmenen", "kymmentä", "toista", "sataa", "sata", "tuhatta", "tuhat"],
                       key=len, reverse=True)
_EN_WORD = "|".join(sorted([*_EN_NUMBERS, "hundred", "thousand"], key=len, reverse=True))
_FI_WORD = "(?:" + "|".join(_FI_MORPHEMES) + ")+"
_NUMERAL = rf"(?:{_EN_WORD}|{_FI_WORD})"
# One or more numeral words, then (lookahead) a unit: only then is it a quantity.
_NUMERAL_RUN = re.compile(
    rf"\b(?:a\s+)?(?:{_NUMERAL}[\s-]+(?:and[\s-]+)?)*{_NUMERAL}\b(?=\s*{_UNIT})", re.IGNORECASE
)


def _finnish_number(word: str) -> int | None:
    """Parse one Finnish numeral word ("kaksikymmentäviisi", "satakaksikymmentä", "kaksikymmentätuhatta")."""
    total = acc = pending = 0
    rest = word.lower()
    if not rest:
        return None
    while rest:
        morpheme = next((m for m in _FI_MORPHEMES if rest.startswith(m)), None)
        if morpheme is None:
            return None
        rest = rest[len(morpheme):]
        if morpheme in _FI_UNITS:
            pending += _FI_UNITS[morpheme]
        elif morpheme == "toista":
            acc, pending = acc + pending + 10, 0
        elif morpheme == "kymmenen":
            acc += 10
        elif morpheme == "kymmentä":
            acc, pending = acc + (pending or 1) * 10, 0
        elif morpheme == "sata":
            acc += 100
        elif morpheme == "sataa":
            acc, pending = acc + (pending or 1) * 100, 0
        elif morpheme == "tuhat":
            total, acc, pending = total + 1000, 0, 0
        else:  # tuhatta
            total, acc, pending = total + (acc + pending or 1) * 1000, 0, 0
    value = total + acc + pending
    return value or None


def _english_number(words: list[str]) -> int | None:
    total = group = 0
    seen = False
    for raw in words:
        word = raw.lower()
        if word == "and" and seen:
            continue
        if word in _EN_NUMBERS:
            group += _EN_NUMBERS[word]
        elif word == "hundred":
            group = (group or 1) * 100
        elif word == "thousand":
            total, group = total + (group or 1) * 1000, 0
        elif word == "a" and not seen:
            group = 0
        else:
            return None
        seen = True
    value = total + group
    return value or None


def _number_value(phrase: str) -> int | None:
    words = re.split(r"[\s-]+", phrase.strip())
    if len(words) == 1 and (fi := _finnish_number(words[0])) is not None:
        return fi
    return _english_number(words)


def _normalise_words(text: str) -> str:
    """Write number words before a unit as digits, and split "luomumaitoa" into "luomu maitoa"."""
    text = re.sub(r"\b(luomu)(?=[^\W\d_]{3,})", r"\1 ", text, flags=re.IGNORECASE)

    def as_digits(match: re.Match) -> str:
        value = _number_value(match.group(0))
        return str(value) if value is not None else match.group(0)

    return _NUMERAL_RUN.sub(as_digits, text)


def _strip_lead(text: str) -> str:
    return re.sub(
        r"^.*?\b(?:we need|i need|we'd like|i'd like|we want|i want|please send|send us|order|"
        r"tarvitsemme|tarvitsen|tarvitaan|tarvitsisimme|haluamme|haluaisimme|tilaamme|tilaisimme|tilataan|"
        r"toimittakaa|lähettäkää)\b",
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
    # T52: a stated attribute is required, never silently a soft preference.
    if any(p.kind == "ORGANIC" for p in preferences):
        out.append("Organic required: proven only by an organic label, otherwise offered as a substitute")
    # T87: a stated product form is shown as a chip ("Grated", "Sliced", ...).
    for p in preferences:
        if p.kind == "OTHER" and p.value.lower() in FORMS_BY_LABEL:
            out.append(p.value)
    # A stated fat % is already shown (and editable) as its own preference chip (T24); the planner treats it
    # as required (T52), so no second chip for the same value.
    return out


# "cheddar made from cow milk": the milk the product is made from (the "from" is a stopword by now).
_MADE_FROM_MILK_RE = re.compile(
    r"\s*\bmade\s+(?:from\s+|of\s+|with\s+)?(?:(?:cow|cows|goat|goats|sheep)['’]?s?\s+)?milk\b"
    r"|\s*\b(?:from\s+)?(?:cow|cows|goat|goats|sheep)['’]?s?\s+milk\b",
    re.IGNORECASE,
)
_PLAIN_MILK_RE = re.compile(r"(?:[^\W\d_]+\s+)*milk(?:\s+drink)?", re.IGNORECASE)
# T74: the product type a milk-free alternative must keep (English, Finnish partitive of "… alternative").
_DAIRY_TYPES: tuple[tuple[re.Pattern, str, str], ...] = tuple(
    (re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE), english, finnish)
    for pattern, english, finnish in (
        (r"ice\s+cream", "ice cream", "jäätelövaihtoehdon"),
        (r"sour\s+cream", "sour cream", "kermaviilivaihtoehdon"),
        (r"cheese|cheddar|emmental|edam|gouda|mozzarella|feta|brie|camembert|halloumi|parmesan|tilsit",
         "cheese", "juustovaihtoehdon"),
        (r"yoghurt|yogurt", "yoghurt", "jogurttivaihtoehdon"),
        (r"quark", "quark", "rahkavaihtoehdon"),
        (r"cream", "cream", "kermavaihtoehdon"),
        (r"butter", "butter", "voivaihtoehdon"),
    )
)


def _label_notes(preferences: list[Preference]) -> list[str]:
    """T74: a certification is kept out of the product name and said in the assumptions, never dropped."""
    labels = dict.fromkeys(
        _CERTIFICATION_NAMES[p.value] for p in preferences if p.kind == "LABEL" and p.value in _CERTIFICATION_NAMES
    )
    return [f"{label} label preferred: {label}-labelled products are ranked first (not a hard requirement)"
            for label in labels]


def _clean_name(what: object) -> object:
    """The LLM's product name without certification, occasion or instruction words (T74, T87)."""
    if not isinstance(what, str):
        return what
    cleaned = _cut_instruction_clauses(what, detect_instructions(what))
    for pattern, _code, _label in _CERTIFICATIONS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _OCCASION_CONTEXT_FI_RE.sub(" ", _OCCASION_CONTEXT_RE.sub(" ", cleaned))
    cleaned = " ".join(cleaned.split()).strip(" ,;-.")
    if cleaned and (_ALT_SPLIT_RE.search(cleaned) or _is_finnish(cleaned)):
        normalised = _what(cleaned, finnish=_is_finnish(cleaned))
        if normalised:
            return normalised
    return cleaned or what


def _contradiction(what: str, requirements: list[Requirement], finnish: bool = False) -> str | None:
    # Only milk itself (§7.10: "lactose-free milk that is milk-free") or a product the buyer says is made from
    # milk ("milk-free cheddar made from cow milk"). Milk-free cheese, cream and yoghurt alternatives exist,
    # so those alone are not contradictions.
    if not _MILK_RE.search(what) or _PLANT_RE.search(what):
        return None
    conflict = next(
        (r for r in requirements if (r.kind, r.code) in (("AVOID_ALLERGEN", "AM"), ("CLAIM", "VEGAN"))), None
    )
    if conflict is None:
        return None
    base = " ".join(_MADE_FROM_MILK_RE.sub(" ", what).split()) or what
    made_from_milk = base != what
    plain_milk = not made_from_milk and bool(_PLAIN_MILK_RE.fullmatch(base))
    # The alternative keeps the product type (a milk-free cheese for a cheese), or names none (T74).
    kind = None if plain_milk else next((t for t in _DAIRY_TYPES if t[0].search(base)), None)
    if finnish:
        label = "maidoton" if conflict.code == "AM" else "vegaaninen"
        if plain_milk:
            return (
                f"Maito on maitotuote, joten se ei voi olla {label}: tarvitsetteko laktoositonta maitoa "
                "vai maidottoman vaihtoehdon, kuten kaurajuoman?"
            )
        other = f"maidottoman {kind[2]}" if kind else "maidottoman vaihtoehdon"
        return (
            f"Maidosta tehty tuote on maitotuote, joten se ei voi olla {label}: tarvitsetteko laktoositonta "
            f"tuotetta vai {other}?"
        )
    label = _REQUIREMENT_LABELS[conflict.code]
    subject = f"{base} made from milk" if made_from_milk else base
    if plain_milk:
        other = "a milk-free alternative such as oat drink"
    elif kind is not None:
        other = f"a {label} {kind[1]} alternative"
    else:
        other = f"a {label} alternative"
    return (
        f"{subject[:1].upper()}{subject[1:]} is a dairy product, so it cannot also be {label}: "
        f"do you need lactose-free {base}, or {other}?"
    )


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
                "what": _clean_name(entry.get("what")),
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
    # Deterministic deadline and request-wide requirement notes are never dropped by the LLM.
    assumptions += [note for note in fallback.notes if note not in assumptions]
    request = StructuredRequest.model_validate(
        {"requestText": text, "requestedDate": requested, "items": items, "assumptions": assumptions}
    )
    # Request-wide constraints apply even when the LLM splits/merges items differently from the fallback.
    request = request.model_copy(update={"items": [
        item.model_copy(update={"requirements": [*item.requirements, *(
            r for r in fallback.global_requirements if r not in item.requirements
        )]}) for item in request.items
    ]})
    # A hard requirement, certification label or product form the deterministic reading found is never dropped by the LLM.
    if fallback.request is not None and len(fallback.request.items) != len(request.items) and any(
        extract_requested_base(item.what) for item in fallback.request.items
    ):
        raise ValueError("LLM changed item boundaries for a stated product base")
    if fallback.request is not None and len(fallback.request.items) == len(request.items):
        merged = []
        for llm_item, det_item in zip(request.items, fallback.request.items, strict=True):
            extra = [r for r in det_item.requirements if r not in llm_item.requirements]
            labels = [
                p
                for p in det_item.preferences
                if (p.kind in ("LABEL", "PACK_SIZE") or (p.kind == "OTHER" and p.value.lower() in FORMS_BY_LABEL))
                and p not in llm_item.preferences
            ]
            merged.append(llm_item.model_copy(update={
                "requirements": [*llm_item.requirements, *extra],
                "preferences": [*llm_item.preferences, *labels],
            }))
        for det_item in fallback.request.items:
            for p in det_item.preferences:
                if p.kind == "OTHER" and p.value.lower() in FORMS_BY_LABEL and p.value not in assumptions:
                    assumptions.append(p.value)
        request = request.model_copy(update={"items": merged, "assumptions": assumptions})
        # A recognised compound/base and its form cannot be weakened by a model's generic head word.
        for llm_item, det_item in zip(request.items, fallback.request.items, strict=True):
            if base := extract_requested_base(det_item.what):
                if extract_requested_base(llm_item.what) != base:
                    raise ValueError("LLM dropped or changed a stated product base")
            if extract_requested_form(det_item.what, include_bases=False):
                det_concepts = resolve_query_concepts(det_item.what)
                llm_concepts = resolve_query_concepts(llm_item.what)
                if det_concepts and (not llm_concepts or det_concepts[0].concept_id != llm_concepts[0].concept_id):
                    raise ValueError("LLM changed the product noun modified by a stated form")
    if any(_form_only(item.what) or _BARE_CONTAINER_RE.fullmatch(item.what) for item in request.items):
        raise ValueError("LLM returned a form or pack container as a product")
    if (fallback.request is not None and _PACK_PHRASE_RE.search(_normalise_words(text))
            and len(request.items) != len(fallback.request.items)):
        raise ValueError("LLM turned a pack-size phrase into an item")
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
    local = now.astimezone(HELSINKI)
    today = local.date()
    finnish = _is_finnish(text)
    ignored = detect_instructions(text)
    # The gateway's O4 rule is authoritative (any agent can call the gateway): whenever it flags the
    # text, the agent reports INSTRUCTION_IGNORED too, so the two never disagree in the unsafe direction.
    gateway_flag = detect_instruction(text)
    if gateway_flag and not ignored:
        logger.warning("INSTRUCTION_IGNORED category=GATEWAY_RULE_O4")
    reasons = [ReasonCode.INSTRUCTION_IGNORED] if ignored or gateway_flag else []

    if len(text) > MAX_TEXT_LENGTH:
        return ParseResult(
            request=None,
            reasons=[*reasons, ReasonCode.CLARIFICATION_NEEDED],
            clarificationQuestion=(
                "Voisitteko lyhentää pyynnön koskemaan vain tuotteita, määriä ja toimituspäivää?" if finnish
                else "Could you shorten the request to the products, quantities and delivery date?"
            ),
            ignoredInstructions=ignored,
            mode="FALLBACK",
            fallbackReason="too_long",
        )

    fallback = _fallback(text, today, ignored, finnish, local.strftime("%H:%M"))
    request, question = fallback.request, fallback.question
    mode: ParseMode = "FALLBACK"
    fallback_reason: str | None = "no_llm"
    if interpreter is not None:
        try:
            clean_for_llm = _cut_instruction_clauses(text, ignored) if ignored else text
            raw = call_with_deadline(interpreter, clean_for_llm, today, deadline_s)
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
            (q for item in request.items if (q := _contradiction(item.what, item.requirements, finnish))), None
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
