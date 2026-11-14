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
from app.catalog.vocab import FI_SV_TO_EN_SYNONYMS
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
    ("l", r"litres?|liters?|litr(?:aa|an|oja|a)|ltr|l"),
    ("ml", r"ml|millilitres?|milliliters?|millilitr\w*"),
    ("dl", r"dl|decilitres?|deciliters?|desilitr\w*"),
    ("kg", r"kgs?|kilos?|kilograms?|kilogrammes?|kilogramm\w*|kilo(?:a|n)"),
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
    ((Requirement(kind="AVOID_ALLERGEN", code="NL"),), r"lupin", r"lupiini"),
    ((Requirement(kind="AVOID_ALLERGEN", code="AU"),), r"sulphites?|sulfites?", r"sulfiiti|sulfiit"),
    ((Requirement(kind="AVOID_ALLERGEN", code="UW"),), r"wheat", r"vehnä"),
)
_NEGATION_EN = (r"(?:no|without|free\s+(?:of|from)|not\s+containing|contains?\s+no|zero|avoid(?:ing)?|"
                r"excluding|exclude)\s+(?:any\s+|added\s+|traces\s+of\s+)?")
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
# Purpose clauses ("for the school", "for our kitchen") describe the buyer, not the product.
_PURPOSE_RE = re.compile(
    r"\bfor\s+(?:the|our|a|an|my|this|their)?\s*[^\W\d_]+(?:\s+(?:kitchen|canteen|cafeteria|restaurant|"
    r"staff|team|event|lunch|dinner|breakfast|guests|week|weekend))?",
    re.IGNORECASE,
)

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
    "as", "usual", "thanks", "thank", "you", "hi", "hello", "hey", "kiitos", "moi", "hei", "kuten", "yleensä",
    "tavallista", "aina", "tehty", "tehtyä", "tehtyjä", "valmistettu", "valmistettua",
    "ensi", "seuraavaksi", "vähintään", "yhteensä", "olisi", "tarvittaisiin", "tilataan", "haluaisimme",
    "tilaisimme", "toimittakaa", "lähettäkää", "kiitti", "tällä", "kertaa",
    # Time, urgency, meal and vagueness words describe the delivery or the occasion, never the product.
    "immediately", "urgently", "urgent", "automatically", "now", "right", "away", "quickly", "soon", "possible",
    "lunch", "dinner", "breakfast", "brunch", "supper", "severe", "serious",
    "something", "anything", "whatever",
    "heti", "pian", "nopeasti", "kiireellisesti", "kiireesti", "pikaisesti", "välittömästi",
    "automaattisesti", "mahdollisimman", "lounaaksi", "lounaalle", "päivälliseksi", "aamiaiseksi",
    "illalliseksi", "välipalaksi", "jälkiruoaksi", "jälkiruuaksi", "vakava", "vakavan",
    "jotain", "jotakin", "mitä", "tahansa",
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
    ("mysli", "muesli"),
    ("kerma", "cream"),
    ("juusto", "cheese"),
    ("jogurt", "yoghurt"),
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
        (r"(?:kirnu|meijeri|ruoka)?voi(?:ta|n|hin|ksi)?", "butter"),
        (r"kermavaah\w*|(?:kuohu|vispi)kerm\w*", "whipping cream"),
        (r"ruokakerm\w*", "cooking cream"),
        (r"kahvikerm\w*", "coffee cream"),
        (r"kermaviil\w*|smetan\w*|ranskankerm\w*", "sour cream"),
        (r"tuorejuust\w*", "cream cheese"),
        (r"emmentaljuust\w*", "Emmental cheese"),
        (r"lohifile\w*", "salmon fillet"),
        (r"kirjolo(?:hi|ht|he)\w*", "rainbow trout"),
        (r"lohi|lohta|lohen|lohia|lohet|loheen|lohesta", "salmon"),
        (r"(?:kanan)?muna(?:a|t|n)?|(?:kanan)?munia|(?:kanan)?munien", "eggs"),
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
# "product no. 465039" and the Finnish ordinal date "20. marraskuuta" stay one sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])(?<!\d\.)\s+(?!\d)|\n+")
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


def _translate(token: str) -> tuple[str, bool]:
    """Finnish product noun (any inflection) -> (English, True); anything else (brands, names) as written.

    Whole-word inflections come first, then the parser's core stems, then the catalogue's FI/SV stem table
    (T04), so the request and the search speak the same vocabulary.
    """
    lower = token.lower()
    if compound := _compound(lower):
        return compound, True
    for pattern, english in _FINNISH_FORMS:
        if pattern.fullmatch(lower):
            return english, True
    for stem, english in _FINNISH_NOUNS:
        if lower.startswith(stem):
            return english, True
    for stem, english_terms in FI_SV_TO_EN_SYNONYMS:
        if len(stem) >= 4 and lower.startswith(stem):
            return english_terms[0], True
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
    for pattern, found in _REQUIREMENT_RES:
        if pattern.search(rest):
            requirements.extend(r for r in found if r not in requirements)
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
    rest = _HEADCOUNT_RE.sub(" ", rest)
    rest = _OCCASION_CONTEXT_RE.sub(" ", rest)
    rest = _PURPOSE_RE.sub(" ", rest)
    rest = _DESTINATION_RE.sub(" ", rest)
    rest = _BENIGN_NEGATION_RE.sub(" ", rest)
    return rest, requirements, preferences, product_id, packs


def _unknown_negation(text: str) -> str | None:
    """Return a negated word the vocabulary cannot map ("no sugar", "salt-free"), or None."""
    rest = _PRODUCT_ID_RE.sub(" ", text)
    for pattern, _found in _REQUIREMENT_RES:
        rest = pattern.sub(" ", rest)
    for _kind, pattern in _DATE_RES:
        rest = pattern.sub(" ", rest)
    for m in _UNKNOWN_NEGATION_RE.finditer(rest):
        word = (m["word"] or m["free"]).lower()
        if word not in _BENIGN_NEGATED:
            return " ".join(m.group(0).split())
    return None


def _words(rest: str, finnish: bool = False) -> tuple[str | None, list[str]]:
    """The item name in English, plus the buyer's own words for it (for a question in their language)."""
    raw = [t for t in _PUNCTUATION.sub(" ", rest).split()
           if t.lower().strip("'’") not in _STOPWORDS and not re.fullmatch(r"[-®™'’]+", t)]
    tokens = [(t, *_translate(t)) for t in raw]
    if finnish:
        tokens = _cut_finnish_tail(tokens)
    words: list[str] = []
    for _original, english, _known in tokens:
        if not words or words[-1] != english:
            words.append(english)
    return " ".join(words).strip(" -") or None, [original for original, _e, _k in tokens]


def _is_genitive_word(token: tuple[str, str, bool]) -> bool:
    original, _english, known = token
    return not known and original[:1].islower() and len(original) >= 4 and original.lower().endswith("n")


def _cut_finnish_tail(tokens: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    """Drop destination and circumstance words ("koulukeittiöön yhdellä oppilaalla") from a Finnish item."""

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
                return tokens[:n]
        # A genitive left dangling at the end modified something already removed ("… koulun [lounaalle]").
        end = len(tokens)
        while end - 1 > known_at[-1] and _is_genitive_word(tokens[end - 1]):
            end -= 1
        return tokens[:end]
    kept = [t for t in tokens if not place(t[0], t[2])]
    return kept or tokens


def _what(rest: str, finnish: bool = False) -> str | None:
    return _words(rest, finnish)[0]


@dataclass
class _Fallback:
    request: StructuredRequest | None
    question: str | None
    safety_question: str | None  # unsupported unit or contradiction: wins over any LLM result
    explicit_date: date | None
    items: list[_ItemDraft]
    notes: list[str] = field(default_factory=list)  # request-level facts any reading must keep (deadline)


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
    # Request text is data: instruction-like spans are cut out before anything else reads the text.
    clean = text
    for finding in sorted(ignored, key=lambda f: f.start, reverse=True):
        clean = clean[: finding.start] + ", " + clean[finding.end :]
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
    for sentence in _SENTENCE_SPLIT.split(clean):
        if not _QUANTITY_RE.search(sentence):
            # A sentence without a quantity can still carry a hard requirement ("It must be gluten-free").
            for pattern, found in _REQUIREMENT_RES:
                if pattern.search(sentence):
                    extra_requirements.extend(r for r in found if r not in extra_requirements)
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
            similar = bool(_SIMILAR_RE.search(fragment))
            fragment = _SIMILAR_RE.sub(" ", fragment)
            first = _QUANTITY_RE.search(fragment)
            assert first is not None
            before, after = fragment[: first.start()], fragment[first.end() :]
            rest_after, req_a, pref_a, id_a, packs = _extract(after)
            rest_before, req_b, pref_b, id_b, _ = _extract(before)
            requirements = req_b + [r for r in req_a if r not in req_b]
            what = _what(rest_after, finnish) or _what(_strip_lead(rest_before), finnish)
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
            if similar:
                notes.append(f"Similar products accepted for {what or 'this item'}")
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
        what, originals = _words(stripped, finnish) if (stripped != rest or found_requirements) else (None, [])
        question = (
            _how_much_question(what, originals, finnish) if what
            else _what_question(clean, finnish, dated=explicit is not None or deadline is not None)
        )
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
    if question is None and safety_question is None:
        for item in items:
            if not item.what:
                question = _which_product_question(item.raw_quantity, finnish)
                break

    notes: list[str] = []
    requested = deadline_day or (explicit.value if explicit else today + timedelta(days=1))
    if deadline is not None:
        notes.append(_deadline_note(deadline, requested, today))
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
    explicit_date = deadline_day or (explicit.value if explicit else None)
    return _Fallback(request, question, safety_question, explicit_date, items, notes)


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
    return out


def _contradiction(what: str, requirements: list[Requirement], finnish: bool = False) -> str | None:
    # Only milk itself (§7.10: "lactose-free milk that is milk-free"). Milk-free cheese, cream and
    # yoghurt alternatives exist, so those are not contradictions.
    if _MILK_RE.search(what) and not _PLANT_RE.search(what):
        for requirement in requirements:
            if (requirement.kind, requirement.code) in (("AVOID_ALLERGEN", "AM"), ("CLAIM", "VEGAN")):
                if finnish:
                    label = "maidoton" if requirement.code == "AM" else "vegaaninen"
                    return (
                        f"Maito on maitotuote, joten se ei voi olla {label}: tarvitsetteko laktoositonta maitoa "
                        "vai maidottoman vaihtoehdon, kuten kaurajuoman?"
                    )
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
    # A deadline the deterministic reading found ("tänään klo 14 mennessä") is never dropped by the LLM.
    assumptions += [note for note in fallback.notes if note not in assumptions]
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
