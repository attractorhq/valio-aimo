"""Canonical requirement code sets and alias hints for `Requirement` validation (T93).

Kept in `app.contracts` with zero domain imports; `tests/test_t93.py` verifies exact parity with `app.trust.labels`.
"""

import difflib
from types import MappingProxyType

VALID_CLAIM_CODES: tuple[str, ...] = (
    "AQUACULTURE_STEWARDSHIP_COUNCIL",
    "DOLPHIN_SAFE",
    "EU_ECO_LABEL",
    "EU_ORGANIC_FARMING",
    "FAIR_TRADE_MARK",
    "FINNISH_HEART_SYMBOL",
    "FREE_FROM_ADDITIVES",
    "FREE_FROM_ARTIFICIAL_SWEETENERS",
    "FREE_FROM_COLOURING_AGENTS",
    "FREE_FROM_EGGS",
    "FREE_FROM_FAT",
    "FREE_FROM_GLUTEN",
    "FREE_FROM_GMO",
    "FREE_FROM_LACTOSE",
    "FREE_FROM_PRESERVATIVES",
    "FREE_FROM_SODIUM_SALT",
    "FREE_FROM_SOY",
    "FREE_FROM_SWEETENERS",
    "FREE_FROM_WHEAT",
    "GOODS_FROM_FINLAND_BLUE_SWAN",
    "HIGH_FIBRE",
    "HIGH_PROTEIN",
    "KRAV_MARK",
    "LOW_FAT",
    "LOW_LACTOSE",
    "LOW_SODIUM_SALT",
    "MADE_IN_FINLAND_FLAG_WITH_KEY",
    "MARINE_STEWARDSHIP_COUNCIL_LABEL",
    "NO_ADDED_SUGARS",
    "OFFICIAL_ECO_LABEL_SUN",
    "PUHTAASTI_KOTIMAINEN",
    "RAINFOREST_ALLIANCE",
    "SOIL_ASSOCIATION_ORGANIC_SYMBOL",
    "SVANEN",
    "UTZ_CERTIFIED",
    "VEGAN",
    "VEGETARIAN",
)

VALID_ALLERGEN_CODES: tuple[str, ...] = (
    "ABD",
    "AC",
    "ADQ",
    "AE",
    "AF",
    "AM",
    "AN",
    "AP",
    "AS",
    "AU",
    "AW",
    "AY",
    "BC",
    "BF",
    "BM",
    "BP",
    "CM",
    "GB",
    "GK",
    "GO",
    "GS",
    "ML",
    "NC",
    "NE",
    "NK",
    "NL",
    "NM",
    "NP",
    "NR",
    "NW",
    "PN",
    "PO",
    "SA",
    "SC",
    "SH",
    "SM",
    "SO",
    "SP",
    "SR",
    "ST",
    "SW",
    "UM",
    "UN",
    "UW",
)

_CLAIM_SET: frozenset[str] = frozenset(VALID_CLAIM_CODES)
_ALLERGEN_SET: frozenset[str] = frozenset(VALID_ALLERGEN_CODES)

CLAIM_ALIASES: MappingProxyType[str, str] = MappingProxyType({
    "LACTOSE_FREE": "FREE_FROM_LACTOSE",
    "NO_LACTOSE": "FREE_FROM_LACTOSE",
    "LAKTOOSITON": "FREE_FROM_LACTOSE",
    "LAKTOSFRI": "FREE_FROM_LACTOSE",
    "ML": "FREE_FROM_LACTOSE",
    "GLUTEN_FREE": "FREE_FROM_GLUTEN",
    "NO_GLUTEN": "FREE_FROM_GLUTEN",
    "GLUTEENITON": "FREE_FROM_GLUTEN",
    "GLUTENFRI": "FREE_FROM_GLUTEN",
    "EGG_FREE": "FREE_FROM_EGGS",
    "NO_EGG": "FREE_FROM_EGGS",
    "NO_EGGS": "FREE_FROM_EGGS",
    "MUNATON": "FREE_FROM_EGGS",
    "SOY_FREE": "FREE_FROM_SOY",
    "SOYA_FREE": "FREE_FROM_SOY",
    "NO_SOY": "FREE_FROM_SOY",
    "NO_SOYA": "FREE_FROM_SOY",
    "SOIJATON": "FREE_FROM_SOY",
    "WHEAT_FREE": "FREE_FROM_WHEAT",
    "NO_WHEAT": "FREE_FROM_WHEAT",
    "VEHNÄTÖN": "FREE_FROM_WHEAT",
    "VEHNATON": "FREE_FROM_WHEAT",
    "FAT_FREE": "FREE_FROM_FAT",
    "NON_FAT": "FREE_FROM_FAT",
    "SKIMMED": "FREE_FROM_FAT",
    "RASVATON": "FREE_FROM_FAT",
    "VÄHÄLAKTOOSINEN": "LOW_LACTOSE",
    "VAHALAKTOOSINEN": "LOW_LACTOSE",
    "SUGAR_FREE": "NO_ADDED_SUGARS",
    "NO_SUGAR": "NO_ADDED_SUGARS",
    "NO_ADDED_SUGAR": "NO_ADDED_SUGARS",
    "UNSWEETENED": "NO_ADDED_SUGARS",
    "HIGH_FIBER": "HIGH_FIBRE",
    "FIBER": "HIGH_FIBRE",
    "FIBRE": "HIGH_FIBRE",
    "PROTEIN": "HIGH_PROTEIN",
    "SALT_FREE": "FREE_FROM_SODIUM_SALT",
    "NO_SALT": "FREE_FROM_SODIUM_SALT",
    "SODIUM_FREE": "FREE_FROM_SODIUM_SALT",
    "UNSALTED": "FREE_FROM_SODIUM_SALT",
    "SUOLATON": "FREE_FROM_SODIUM_SALT",
    "LOW_SALT": "LOW_SODIUM_SALT",
    "LOW_SODIUM": "LOW_SODIUM_SALT",
    "VÄHÄSUOLAINEN": "LOW_SODIUM_SALT",
    "VAHASUOLAINEN": "LOW_SODIUM_SALT",
    "PRESERVATIVE_FREE": "FREE_FROM_PRESERVATIVES",
    "NO_PRESERVATIVES": "FREE_FROM_PRESERVATIVES",
    "ADDITIVE_FREE": "FREE_FROM_ADDITIVES",
    "NO_ADDITIVES": "FREE_FROM_ADDITIVES",
    "COLOUR_FREE": "FREE_FROM_COLOURING_AGENTS",
    "COLOR_FREE": "FREE_FROM_COLOURING_AGENTS",
    "NO_COLOURING": "FREE_FROM_COLOURING_AGENTS",
    "NO_COLORING": "FREE_FROM_COLOURING_AGENTS",
    "SWEETENER_FREE": "FREE_FROM_SWEETENERS",
    "NO_SWEETENERS": "FREE_FROM_SWEETENERS",
    "NO_ARTIFICIAL_SWEETENERS": "FREE_FROM_ARTIFICIAL_SWEETENERS",
    "GMO_FREE": "FREE_FROM_GMO",
    "NON_GMO": "FREE_FROM_GMO",
    "VEGAANINEN": "VEGAN",
    "VEGAANI": "VEGAN",
    "KASVIS": "VEGETARIAN",
    "ORGANIC": "EU_ORGANIC_FARMING",
    "LUOMU": "EU_ORGANIC_FARMING",
    "BIO": "EU_ORGANIC_FARMING",
    "EKOLOGISK": "EU_ORGANIC_FARMING",
    "MSC": "MARINE_STEWARDSHIP_COUNCIL_LABEL",
    "MSC_CERTIFIED": "MARINE_STEWARDSHIP_COUNCIL_LABEL",
    "ASC": "AQUACULTURE_STEWARDSHIP_COUNCIL",
    "ASC_CERTIFIED": "AQUACULTURE_STEWARDSHIP_COUNCIL",
    "FAIRTRADE": "FAIR_TRADE_MARK",
    "FAIR_TRADE": "FAIR_TRADE_MARK",
    "REILU_KAUPPA": "FAIR_TRADE_MARK",
    "FINNISH": "GOODS_FROM_FINLAND_BLUE_SWAN",
    "KOTIMAINEN": "GOODS_FROM_FINLAND_BLUE_SWAN",
    "SUOMALAINEN": "GOODS_FROM_FINLAND_BLUE_SWAN",
    "HYVAA_SUOMESTA": "GOODS_FROM_FINLAND_BLUE_SWAN",
    "HYVÄÄ_SUOMESTA": "GOODS_FROM_FINLAND_BLUE_SWAN",
    "AVAINLIPPU": "MADE_IN_FINLAND_FLAG_WITH_KEY",
    "KEY_FLAG": "MADE_IN_FINLAND_FLAG_WITH_KEY",
    "HEART_SYMBOL": "FINNISH_HEART_SYMBOL",
    "SYDÄNMERKKI": "FINNISH_HEART_SYMBOL",
    "SYDANMERKKI": "FINNISH_HEART_SYMBOL",
})

_CLAIM_TO_ALLERGEN_HINTS: MappingProxyType[str, str] = MappingProxyType({
    "MILK_FREE": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "DAIRY_FREE": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "FREE_FROM_MILK": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "FREE_FROM_DAIRY": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "NO_MILK": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "NO_DAIRY": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "MAIDOTON": "kind='AVOID_ALLERGEN' with code='AM' (milk)",
    "NUT_FREE": "kind='AVOID_ALLERGEN' with code='AN' (tree nuts) and code='AP' (peanuts)",
    "FREE_FROM_NUTS": "kind='AVOID_ALLERGEN' with code='AN' (tree nuts) and code='AP' (peanuts)",
    "NO_NUTS": "kind='AVOID_ALLERGEN' with code='AN' (tree nuts) and code='AP' (peanuts)",
    "PÄHKINÄTÖN": "kind='AVOID_ALLERGEN' with code='AN' (tree nuts) and code='AP' (peanuts)",
    "PAHKINATON": "kind='AVOID_ALLERGEN' with code='AN' (tree nuts) and code='AP' (peanuts)",
    "PEANUT_FREE": "kind='AVOID_ALLERGEN' with code='AP' (peanuts)",
    "FREE_FROM_PEANUTS": "kind='AVOID_ALLERGEN' with code='AP' (peanuts)",
    "NO_PEANUTS": "kind='AVOID_ALLERGEN' with code='AP' (peanuts)",
    "FISH_FREE": "kind='AVOID_ALLERGEN' with code='AF' (fish)",
    "SESAME_FREE": "kind='AVOID_ALLERGEN' with code='AS' (sesame)",
})

ALLERGEN_ALIASES: MappingProxyType[str, str] = MappingProxyType({
    "MILK": "AM",
    "DAIRY": "AM",
    "MAITO": "AM",
    "EGG": "AE",
    "EGGS": "AE",
    "MUNA": "AE",
    "KANANMUNA": "AE",
    "FISH": "AF",
    "KALA": "AF",
    "CRUSTACEANS": "AC",
    "SHELLFISH": "AC",
    "MOLLUSCS": "UM",
    "MOLLUSKS": "UM",
    "NUT": "AN",
    "NUTS": "AN",
    "TREE_NUT": "AN",
    "TREE_NUTS": "AN",
    "PÄHKINÄ": "AN",
    "PAHKINA": "AN",
    "PEANUT": "AP",
    "PEANUTS": "AP",
    "MAAPÄHKINÄ": "AP",
    "SESAME": "AS",
    "SEESAMI": "AS",
    "SULPHITES": "AU",
    "SULFITES": "AU",
    "GLUTEN": "AW",
    "CEREALS": "AW",
    "GLUTEENI": "AW",
    "SOY": "AY",
    "SOYA": "AY",
    "SOIJA": "AY",
    "CELERY": "BC",
    "SELLERI": "BC",
    "MUSTARD": "BM",
    "SINAPPI": "BM",
    "LUPIN": "NL",
    "LUPIINI": "NL",
    "LACTOSE": "ML",
    "LAKTOOSI": "ML",
    "WHEAT": "UW",
    "VEHNÄ": "UW",
    "VEHNA": "UW",
    "BARLEY": "GB",
    "OHRA": "GB",
    "RYE": "NR",
    "RUIS": "NR",
    "OAT": "GO",
    "OATS": "GO",
    "KAURA": "GO",
    "SPELT": "GS",
    "KAMUT": "GK",
    "ALMOND": "SA",
    "ALMONDS": "SA",
    "MANTELI": "SA",
    "HAZELNUT": "SH",
    "HAZELNUTS": "SH",
    "CASHEW": "SC",
    "CASHEWS": "SC",
    "PECAN": "SP",
    "PECANS": "SP",
    "BRAZIL_NUT": "SR",
    "BRAZIL_NUTS": "SR",
    "PISTACHIO": "ST",
    "PISTACHIOS": "ST",
    "WALNUT": "SW",
    "WALNUTS": "SW",
    "MACADAMIA": "SM",
    "CORN": "NM",
    "MAIZE": "NM",
    "PEA": "NE",
    "PEAS": "NE",
    "PULSES": "NP",
    "COCOA": "NC",
    "CHICKEN": "CM",
    "BEEF": "BF",
    "PORK": "PO",
    "CARROT": "NW",
    "CORIANDER": "NK",
    "PINE_NUT": "PN",
    "PINE_NUTS": "PN",
    "SUNFLOWER": "SO",
    "POPPY": "ABD",
    "GLUTAMATE": "ADQ",
})


def _normalise_key(raw: str) -> str:
    return "_".join(raw.strip().upper().replace("-", "_").split())


def validate_claim_code(code: str) -> str:
    if code in _CLAIM_SET:
        return code
    norm = _normalise_key(code)
    hint: str | None = None
    if norm in _CLAIM_SET:
        hint = f"did you mean {norm}?"
    elif norm in CLAIM_ALIASES:
        hint = f"did you mean {CLAIM_ALIASES[norm]}?"
    elif norm in _CLAIM_TO_ALLERGEN_HINTS:
        hint = f"did you mean {_CLAIM_TO_ALLERGEN_HINTS[norm]}?"
    elif norm in _ALLERGEN_SET:
        hint = f"did you mean kind='AVOID_ALLERGEN' with code='{norm}'?"
    else:
        close = difflib.get_close_matches(norm, VALID_CLAIM_CODES, n=1, cutoff=0.6)
        if close:
            hint = f"did you mean {close[0]}?"
    hint_part = f"; {hint}" if hint else "."
    valid_list = ", ".join(VALID_CLAIM_CODES)
    raise ValueError(
        f"Unknown CLAIM requirement code {code!r}{hint_part} Valid CLAIM codes: {valid_list}."
    )


def validate_allergen_code(code: str) -> str:
    if code in _ALLERGEN_SET:
        return code
    norm = _normalise_key(code)
    hint: str | None = None
    if norm in _ALLERGEN_SET:
        hint = f"did you mean {norm}?"
    elif norm == "X99":
        hint = "X99 is a supplier declaration sentinel (EU-14 free-from), not an allergen code to avoid."
    elif norm in ALLERGEN_ALIASES:
        target = ALLERGEN_ALIASES[norm]
        if target == "ML":
            hint = "did you mean ML (or kind='CLAIM' with code='FREE_FROM_LACTOSE')?"
        else:
            hint = f"did you mean {target}?"
    elif norm in _CLAIM_SET:
        hint = f"did you mean kind='CLAIM' with code='{norm}'?"
    elif norm in CLAIM_ALIASES:
        hint = f"did you mean kind='CLAIM' with code='{CLAIM_ALIASES[norm]}'?"
    else:
        close = difflib.get_close_matches(norm, VALID_ALLERGEN_CODES, n=1, cutoff=0.75)
        if close:
            hint = f"did you mean {close[0]}?"
    hint_part = f"; {hint}" if hint else "."
    valid_list = ", ".join(VALID_ALLERGEN_CODES)
    raise ValueError(
        f"Unknown AVOID_ALLERGEN requirement code {code!r}{hint_part} Valid AVOID_ALLERGEN codes: {valid_list}."
    )
