"""Multilingual FI ↔ EN ↔ SV vocabulary, Finnish compound matching, and inline requirement extraction."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.contracts import Preference, Requirement


@dataclass(frozen=True)
class ProductConcept:
    concept_id: str
    category_prefixes: tuple[str, ...]
    en_terms: tuple[str, ...]
    fi_stems: tuple[str, ...]
    sv_stems: tuple[str, ...]
    parent_concept: str | None = None
    is_food: bool = True
    exclude_stems: tuple[str, ...] = ()
    prefer_plain_over: tuple[str, ...] = ()


PRODUCT_CONCEPTS: tuple[ProductConcept, ...] = (
    ProductConcept(
        concept_id="oat_drink",
        category_prefixes=("10402",),
        en_terms=("oat drink", "oat milk", "oatdrink", "oat beverage"),
        fi_stems=("kaurajuom", "kauramaito", "kauramaido"),
        sv_stems=("havredryck", "havremjölk"),
        exclude_stems=("jogurt", "yoghurt", "fraiche", "cooking", "ruokakerma", "välipala", "snack", "dessert"),
        prefer_plain_over=(
            "barista", "ikaffe", "choco", "chocolate", "suklaa", "vanilla", "vanilja",
            "caramel", "kinuski", "hazelnut", "pähkinä", "pumpkin", "gingerbread",
            "piparkakku", "cookie", "cardamom", "kardemumma", "20ml",
        ),
    ),
    ProductConcept(
        concept_id="soy_drink",
        category_prefixes=("10402",),
        en_terms=("soy drink", "soya drink", "soy milk", "soya milk"),
        fi_stems=("soijajuom", "soijamaito"),
        sv_stems=("sojadryck", "sojamjölk"),
        exclude_stems=("jogurt", "yoghurt", "dessert", "fraiche"),
    ),
    ProductConcept(
        concept_id="almond_drink",
        category_prefixes=("10402",),
        en_terms=("almond drink", "almond milk"),
        fi_stems=("mantelijuom", "mantelimaito"),
        sv_stems=("mandeldryck", "mandelmjölk"),
    ),
    ProductConcept(
        concept_id="coconut_milk",
        category_prefixes=("10402",),
        en_terms=("coconut milk", "coconut drink"),
        fi_stems=("kookosmaito", "kookosjuom"),
        sv_stems=("kokosmjölk", "kokosdryck"),
    ),
    ProductConcept(
        concept_id="milk_powder",
        category_prefixes=("104",),
        en_terms=("milk powder", "powdered milk", "dried milk"),
        fi_stems=("maitojauhe",),
        sv_stems=("mjölkpulver",),
    ),
    ProductConcept(
        concept_id="milk",
        category_prefixes=("10400",),
        en_terms=("milk drink", "milkdrink", "semi skimmed milk", "skimmed milk", "whole milk", "milk"),
        fi_stems=("kevytmaitojuom", "maitojuom", "kevytmaito", "rasvaton maito", "täysmaito", "maito", "maido"),
        sv_stems=("lättmjölksdryck", "mjölkdryck", "lättmjölk", "skummjölk", "helmjölk", "mjölk"),
        exclude_stems=(
            "kookos", "coconut", "kaura", "oat", "soija", "soy", "manteli", "almond", "jauhe", "powder",
            "milk chocolate", "maitosuklaa", "mjölkchoklad",
        ),
        prefer_plain_over=("coffee", "kahvi", "cafè", "cafe", "latte", "cocoa", "kaakao", "choco", "portion", "2 cl"),
    ),
    ProductConcept(
        concept_id="cream",
        category_prefixes=("10401", "10406"),
        en_terms=("whipping cream", "cooking cream", "coffee cream", "cream"),
        fi_stems=("kuohukerma", "ruokakerma", "kahvikerma", "vispikerma", "kerma"),
        sv_stems=("vispgrädde", "matgrädde", "kaffegrädde", "grädde"),
        exclude_stems=(
            "sour cream", "kermaviili", "ice cream", "jäätelö", "cream cheese", "tuorejuusto", "kermajuust",
            "cream slice", "cream powder", "kermajauhe",
        ),
    ),
    ProductConcept(
        concept_id="sour_cream",
        category_prefixes=("10419", "10418"),
        en_terms=("sour cream", "creme fraiche", "crème fraîche"),
        fi_stems=("kermaviili", "ranskankerma", "smetana"),
        sv_stems=("gräddfil", "crème fraiche", "smetana"),
    ),
    ProductConcept(
        concept_id="yoghurt",
        category_prefixes=("10412", "10413"),
        en_terms=("yoghurt", "yogurt"),
        fi_stems=("jogurt", "jugurt"),
        sv_stems=("yoghurt",),
    ),
    ProductConcept(
        concept_id="quark",
        category_prefixes=("10430",),
        en_terms=("quark", "curd"),
        fi_stems=("maitorahka", "rahka"),
        sv_stems=("kvarg",),
    ),
    ProductConcept(
        concept_id="butter",
        category_prefixes=("105",),
        en_terms=("butter", "margarine", "fat spread", "vegetable spread", "spread"),
        fi_stems=("voi", "margariin", "levite", "rasvaseos"),
        sv_stems=("smör", "margarin", "matfett"),
        exclude_stems=(
            "peanut butter", "maapähkinävoi", "jordnötssmör", "voitaikina", "butter chicken",
            "cheese spread", "hazelnut spread", "chocolate spread", "nut spread", "cocoa spread",
            "kaakaolevite", "pähkinälevite", "juustolevite", "hummus",
        ),
    ),
    ProductConcept(
        concept_id="oil",
        category_prefixes=("105",),
        en_terms=("rapeseed oil", "olive oil", "cooking oil", "frying oil", "sunflower oil", "oil"),
        fi_stems=("rypsiöljy", "oliiviöljy", "ruokaöljy", "paistoöljy", "öljy"),
        sv_stems=("rapsolja", "olivolja", "matolja", "olja"),
    ),
    # T74: the most specific type wins over its head word ("cream cheese" is not cream, "milk powder" is
    # not milk). Specific concepts come before generic ones, and the longest matched term ranks first.
    ProductConcept(
        concept_id="cream_cheese",
        category_prefixes=("106",),
        en_terms=("cream cheese",),
        # Not "kermajuusto": in Finnish that is a semi-hard cheese (Valio sells it as tilsit), see TYPE_QUALIFIER_GROUPS.
        fi_stems=("tuorejuusto",),
        sv_stems=("färskost",),
    ),
    ProductConcept(
        concept_id="processed_cheese",
        category_prefixes=("106",),
        en_terms=("processed cheese",),
        fi_stems=("sulatejuust",),
        sv_stems=("smältost",),
    ),
    ProductConcept(
        concept_id="cheese",
        category_prefixes=("106",),
        en_terms=(
            "cheese", "cheddar", "mozzarella", "emmental", "feta", "tilsit", "edam", "gouda", "brie", "camembert",
            "halloumi", "parmesan", "gorgonzola", "roquefort", "havarti", "maasdam", "mascarpone", "ricotta",
        ),
        fi_stems=("juusto", "emmental", "mozzarella", "salaattijuusto"),
        sv_stems=("ost", "mozzarella", "salladsost"),
    ),
    ProductConcept(
        concept_id="macaroni",
        category_prefixes=("21380",),
        en_terms=("macaroni", "maccheroni", "elbow pasta"),
        fi_stems=("makaron",),
        sv_stems=("makaron",),
        parent_concept="pasta",
        exclude_stems=("laatikko", "casserole", "rice", "riisi"),
    ),
    ProductConcept(
        concept_id="fusilli",
        category_prefixes=("21380",),
        en_terms=("fusilli", "spiral pasta"),
        fi_stems=("fusilli", "kierrepasta"),
        sv_stems=("fusilli", "spiralpasta"),
        parent_concept="pasta",
        exclude_stems=("rice", "riisi"),
    ),
    ProductConcept(
        concept_id="penne",
        category_prefixes=("21380",),
        en_terms=("penne", "penne rigate"),
        fi_stems=("penne", "putkipasta"),
        sv_stems=("penne",),
        parent_concept="pasta",
        exclude_stems=("rice", "riisi"),
    ),
    ProductConcept(
        concept_id="spaghetti",
        category_prefixes=("21380",),
        en_terms=("spaghetti", "spagetti"),
        fi_stems=("spaget",),
        sv_stems=("spaghetti", "spagetti"),
        parent_concept="pasta",
        exclude_stems=("rice", "riisi"),
    ),
    ProductConcept(
        concept_id="lasagne",
        category_prefixes=("21380",),
        en_terms=("lasagne", "lasagna"),
        fi_stems=("lasagne",),
        sv_stems=("lasagne",),
        parent_concept="pasta",
        exclude_stems=("rice", "riisi"),
    ),
    ProductConcept(
        concept_id="pasta",
        category_prefixes=("21380",),
        en_terms=("pasta", "macaroni", "maccheroni", "fusilli", "penne", "spaghetti", "lasagne", "tagliatelle", "rigatoni", "fettuccine", "fettucine"),
        fi_stems=("pasta", "makaron", "fusilli", "penne", "spaget", "lasagne", "tagliatelle"),
        sv_stems=("pasta", "makaron", "fusilli", "penne", "spaghetti", "lasagne", "tagliatelle"),
        exclude_stems=("rice", "riisi", "sushi", "paste", "tahna", "tomaattipyree"),
    ),
    ProductConcept(
        concept_id="noodles",
        category_prefixes=("21381",),
        en_terms=("noodles", "noodle", "udon"),
        fi_stems=("nuudel",),
        sv_stems=("nudl", "nudel"),
    ),
    ProductConcept(
        concept_id="rice",
        category_prefixes=("21340",),
        en_terms=("rice", "risotto rice", "basmati rice", "jasmine rice"),
        fi_stems=("riisi", "risottoriisi", "basmatiriisi", "jasmiiniriisi"),
        sv_stems=("ris", "risottoris", "basmatiris", "jasminris"),
        exclude_stems=("rice paper", "riisipaperi", "risifrutti", "rice cake", "riisikakku"),
    ),
    ProductConcept(
        concept_id="flour",
        category_prefixes=("21300", "21310", "21320"),
        en_terms=("wheat flour", "rye flour", "spelt flour", "flour mix", "flour"),
        fi_stems=("vehnäjauho", "ruisjauho", "spelttijauho", "jauhoseos", "jauho"),
        sv_stems=("vetemjöl", "rågmjöl", "speltmjöl", "mjölmix", "mjöl"),
    ),
    ProductConcept(
        concept_id="oat_flakes",
        category_prefixes=("21370",),
        en_terms=("oat flakes", "rolled oats", "oatmeal", "porridge oats", "flakes"),
        fi_stems=("kaurahiutale", "puurohiutale", "hiutale"),
        sv_stems=("havreflingor", "havregryn", "flingor"),
    ),
    ProductConcept(
        concept_id="muesli",
        category_prefixes=("21373", "21372"),
        en_terms=("muesli", "müesli", "granola", "cereal", "cereals"),
        fi_stems=("mysli", "granola", "murot"),
        sv_stems=("müsli", "granola", "frukostflingor"),
    ),
    ProductConcept(
        concept_id="bread",
        category_prefixes=("109",),
        en_terms=("rye bread", "sourdough bread", "toast", "bread", "rolls"),
        fi_stems=("ruisleipä", "paahtoleipä", "sekaleipä", "sämpylä", "leipä", "limppu"),
        sv_stems=("rågbröd", "rostbröd", "fralla", "bröd", "limpa"),
    ),
    ProductConcept(
        concept_id="crispbread",
        category_prefixes=("212",),
        en_terms=("crispbread", "rye crispbread"),
        fi_stems=("näkkileipä", "näkkäri", "hapankorppu"),
        sv_stems=("knäckebröd", "surskorpa"),
    ),
    ProductConcept(
        concept_id="coffee",
        category_prefixes=("223", "224"),
        en_terms=("ground coffee", "filter coffee", "coffee beans", "instant coffee", "coffee"),
        fi_stems=("suodatinkahvi", "kahvipavut", "pikakahvi", "juhla mokka", "presidentti", "kahvi"),
        sv_stems=("bryggkaffe", "kaffebönor", "snabbkaffe", "kaffe"),
        exclude_stems=("coffee milk", "kahvimaito", "coffee cream", "kahvikerma", "coffeefilter", "suodatinpussi"),
    ),
    ProductConcept(
        concept_id="tea",
        category_prefixes=("225",),
        en_terms=("tea", "green tea", "black tea", "herbal tea"),
        fi_stems=("tee", "vihreä tee", "musta tee"),
        sv_stems=("te", "grönt te", "svart te"),
    ),
    ProductConcept(
        concept_id="sugar",
        category_prefixes=("221",),
        en_terms=("sugar", "syrup", "honey"),
        fi_stems=("sokeri", "siirappi", "hunaja"),
        sv_stems=("socker", "sirap", "honung"),
    ),
    ProductConcept(
        concept_id="juice",
        category_prefixes=("227",),
        en_terms=("orange juice", "apple juice", "juice"),
        fi_stems=("appelsiinimehu", "omenamehu", "täysmehu", "mehu"),
        sv_stems=("apelsinjuice", "äppeljuice", "juice"),
    ),
    ProductConcept(
        concept_id="meat_sauce",
        category_prefixes=("173", "219", "417"),
        en_terms=("minced meat sauce", "meat sauce", "mince sauce", "bolognese sauce", "bolognese"),
        fi_stems=("jauhelihakastik", "lihakastik", "bolognesekastik", "jauhelihabolognese"),
        sv_stems=("köttfärssås", "bolognesesås"),
        exclude_stems=(
            "spaghetti", "spagetti", "pasta", "lasagne", "lasagna", "lasagnette", "pizza",
            "babyfood", "baby food", "lastenateria", "lastenruoka", "dry mix", "kuivaseos",
        ),
    ),
    ProductConcept(
        concept_id="minced_meat",
        category_prefixes=("187", "172", "417"),
        en_terms=(
            "minced meat", "minced beef", "beef mince", "pork mince", "chicken mince",
            "ground meat", "ground beef", "mince",
        ),
        fi_stems=("jauheliha",),
        sv_stems=("malet kött", "köttfärs", "nötfärs", "blandfärs"),
        exclude_stems=(
            "kastike", "sauce", "bolognese", "keitto", "soup", "pihvi", "patty", "patties",
            "burger", "hampurilainen", "mureke", "meatloaf", "pyörykkä", "meatball",
            "laatikko", "vuoka", "casserole", "pizza", "lasagne", "lasagna", "lasagnette",
            "piirakka", "pasteija", "varras", "skewer", "kebakko", "mauste", "seasoning",
            "spice", "maksa", "liver", "kala", "fish", "lohi", "salmon", "kirjoloh",
            "trout", "hauki", "pike",
        ),
    ),
    ProductConcept(
        concept_id="Disposable tableware",
        category_prefixes=("640", "200"),
        en_terms=("plastic cup", "paper cup", "disposable cup", "disposable plate", "tumbler"),
        fi_stems=("kertakäyttömuki", "pahvimuki", "kertakäyttölautanen", "juomalasi"),
        sv_stems=("engångsmugg", "engångstallrik", "plastglas"),
        is_food=False,
    ),
    ProductConcept(
        concept_id="cleaning_waste",
        category_prefixes=("631", "632"),
        en_terms=("waste bag", "trash bag", "detergent", "napkin", "toilet paper", "paper towel"),
        fi_stems=("jätesäkki", "roskapussi", "pesuaine", "lautasliina", "wc-paperi", "käsipyyhe"),
        sv_stems=("sopsäck", "rengöringsmedel", "servett", "toalettpapper"),
        is_food=False,
    ),
)

CONCEPTS_BY_ID: dict[str, ProductConcept] = {c.concept_id: c for c in PRODUCT_CONCEPTS}

# Inline requirement patterns in EN, FI, SV queries (e.g. "gluten-free oat drink", "gluteeniton kaurajuoma", "laktosfri mjölk").
INLINE_REQUIREMENT_PATTERNS: tuple[tuple[re.Pattern[str], Requirement], ...] = (
    (
        re.compile(
            r"\b(?:lactose[\s-]?free|free\s+(?:of|from)\s+lactose|no\s+lactose|without\s+lactose|"
            r"laktoosit(?:on|tom\w*|onta)|ilman\s+laktoosia|laktosfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE"),
    ),
    (
        re.compile(
            r"\b(?:gluten[\s-]?free|glutenfree|free\s+(?:of|from)\s+gluten|no\s+gluten|without\s+gluten|"
            r"gluteenit(?:on|tom\w*|onta)|ilman\s+gluteenia|glutenfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN"),
    ),
    (
        re.compile(
            r"\b(?:milk[\s-]?free|dairy[\s-]?free|non[\s-]?dairy|free\s+(?:of|from)\s+(?:milk|dairy)|"
            r"no\s+(?:milk|dairy)|without\s+(?:milk|dairy)|"
            r"maidot(?:on|tom\w*|onta)|ilman\s+maitoa|mjölkfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AM"),
    ),
    (
        re.compile(
            r"\b(?:egg[\s-]?free|free\s+(?:of|from)\s+eggs?|no\s+eggs?|without\s+eggs?|"
            r"(?:kanan)?munat(?:on|tom\w*|onta)|ilman\s+(?:kanan)?munaa|äggfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AE"),
    ),
    (
        re.compile(
            r"\b(?:peanut[\s-]?free|free\s+(?:of|from)\s+peanuts?|no\s+peanuts?|without\s+peanuts?|"
            r"maapähkinät(?:ön|töm\w*|öntä)|ilman\s+maapähkin\w*|jordnötsfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AP"),
    ),
    (
        re.compile(
            r"\b(?:(?:tree[\s-]?)?nut[\s-]?free|free\s+(?:of|from)\s+(?:tree\s+)?nuts?|no\s+(?:tree\s+)?nuts?|"
            r"without\s+(?:tree\s+)?nuts?|(?<!maa)pähkinät(?:ön|töm\w*|öntä)|ilman\s+pähkin\w*|nötfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AN"),
    ),
    (
        re.compile(
            r"\b(?:soy(?:a)?[\s-]?free|free\s+(?:of|from)\s+soy(?:a)?|no\s+soy(?:a)?|without\s+soy(?:a)?|"
            r"soijat(?:on|tom\w*|onta)|ilman\s+soijaa|sojafri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AY"),
    ),
    (
        re.compile(
            r"\b(?:fish[\s-]?free|free\s+(?:of|from)\s+fish|no\s+fish|without\s+fish|"
            r"kalat(?:on|tom\w*|onta)|ilman\s+kalaa|fiskfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AF"),
    ),
    (
        re.compile(
            r"\b(?:sesame[\s-]?free|free\s+(?:of|from)\s+sesame|no\s+sesame|without\s+sesame|"
            r"seesamit(?:on|tom\w*|onta)|ilman\s+seesamia|sesamfri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="AS"),
    ),
    (
        re.compile(
            r"\b(?:wheat[\s-]?free|free\s+(?:of|from)\s+wheat|no\s+wheat|without\s+wheat|"
            r"vehnät(?:ön|töm\w*|öntä)|ilman\s+vehnää|vetefri\w*)\b",
            re.IGNORECASE,
        ),
        Requirement(kind="AVOID_ALLERGEN", code="UW"),
    ),
    (
        re.compile(r"\b(?:vegan|vegaani\w*|vegansk\w*)\b", re.IGNORECASE),
        Requirement(kind="CLAIM", code="VEGAN"),
    ),
    (
        re.compile(r"\b(?:vegetarian|vegetaar\w*|vegetarisk\w*)\b", re.IGNORECASE),
        Requirement(kind="CLAIM", code="VEGETARIAN"),
    ),
)

# Translations between FI / SV stems and EN canonical terms for general free-text token matching.
FI_SV_TO_EN_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kevytmaitojuom", ("semi skimmed milk drink", "milk drink", "milk")),
    ("tuorekaurajuom", ("fresh oat drink", "oat drink")),
    ("kaurajuom", ("oat drink", "oat")),
    ("kauramaito", ("oat drink", "oat")),
    ("maitojuom", ("milk drink", "milk")),
    ("kevytmaito", ("semi skimmed milk", "milk")),
    ("rasvatonmaito", ("skimmed milk", "milk")),
    ("täysmaito", ("whole milk", "milk")),
    ("maito", ("milk",)),
    ("maido", ("milk",)),
    ("lättmjölksdryck", ("semi skimmed milk drink", "milk drink", "milk")),
    ("mjölkdryck", ("milk drink", "milk")),
    ("lättmjölk", ("semi skimmed milk", "milk")),
    ("skummjölk", ("skimmed milk", "milk")),
    ("helmjölk", ("whole milk", "milk")),
    ("mjölk", ("milk",)),
    ("havredryck", ("oat drink", "oat")),
    ("makaron", ("macaroni", "maccheroni", "pasta")),
    ("spaget", ("spaghetti", "pasta")),
    ("fusilli", ("fusilli", "pasta")),
    ("penne", ("penne", "pasta")),
    ("lasagne", ("lasagne", "pasta")),
    ("pasta", ("pasta",)),
    ("nuudel", ("noodles", "noodle")),
    ("nudl", ("noodles", "noodle")),
    ("riisi", ("rice",)),
    ("vehnäjauho", ("wheat flour", "flour")),
    ("jauho", ("flour",)),
    ("mjöl", ("flour",)),
    ("kaurahiutale", ("oat flakes", "oats")),
    ("hiutale", ("flakes",)),
    ("havregryn", ("oat flakes", "oats")),
    ("havreflingor", ("oat flakes", "oats")),
    ("mysli", ("muesli", "müesli")),
    ("müsli", ("muesli", "müesli")),
    ("kuohukerma", ("whipping cream", "cream")),
    ("ruokakerma", ("cooking cream", "cream")),
    ("kerma", ("cream",)),
    ("grädde", ("cream",)),
    ("kermaviili", ("sour cream",)),
    ("ranskankerma", ("creme fraiche", "sour cream")),
    ("juusto", ("cheese",)),
    ("jogurt", ("yoghurt", "yogurt")),
    ("rahka", ("quark",)),
    ("kvarg", ("quark",)),
    ("ruisleipä", ("rye bread", "bread")),
    ("leipä", ("bread",)),
    ("bröd", ("bread",)),
    ("näkkileipä", ("crispbread",)),
    ("knäckebröd", ("crispbread",)),
    ("kahvi", ("coffee",)),
    ("kaffe", ("coffee",)),
    ("sokeri", ("sugar",)),
    ("socker", ("sugar",)),
    ("hunaja", ("honey",)),
    ("honung", ("honey",)),
    ("suola", ("salt",)),
    ("majoneesi", ("mayonnaise", "mayo", "majonnäs")),
    ("majonnäs", ("mayonnaise", "mayo", "majoneesi")),
    ("jauhelihakastik", ("meat sauce", "bolognese", "minced meat sauce")),
    ("jauheliha", ("minced meat", "mince", "ground meat", "ground beef")),
    ("luomu", ("organic", "ekologisk")),
    ("ekologisk", ("organic", "luomu")),
    ("organic", ("organic", "luomu", "ekologisk")),
)

# T74: brands and product lines in the catalogue's names. A brand phrase is a name ("Oatly No Sugars"), so
# "No" or "Zero" inside it is never an exclusion.
KNOWN_BRANDS: tuple[str, ...] = (
    "Valio", "Arla", "Oatly", "Oddlygood", "Alpro", "Fazer", "Paulig", "Atria", "HK", "Snellman", "Saarioinen",
    "Felix", "Keiju", "Becel", "Flora", "Bregott", "Lipton", "Nordqvist", "Juustoportti", "Pågen", "Elovena",
    "Apetit", "Findus", "Planti", "Yosa", "Philadelphia", "Président", "Castelli", "Violife", "Benecol", "Ingman",
    "Eila", "Oltermanni", "Viola", "Koskenlaskija", "Kippari", "Gefilus", "Profeel", "Oivariini", "Eleplant",
    "Coca-Cola", "Pepsi", "Hartwall", "Barilla", "Panzani", "Santa Maria", "Myllyn Paras", "Pirkka", "Rainbow",
)

QUERY_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "by", "for", "from", "in", "need", "of", "on", "or", "please", "some",
    "the", "to", "until", "we", "with", "i", "want", "order", "buy", "get",
    "tarvitsemme", "tarvitsen", "tarvitaan", "haluamme", "tilaamme", "meille", "ja", "tai",
    "mennessä", "viimeistään", "maanantaiksi", "maanantai", "huomiseksi", "huomenna", "tänään",
    "litraa", "litra", "kiloa", "kilo", "kpl", "laatikkoa", "pakettia", "pussia", "purkkia",
    "litres", "litre", "liters", "liter", "kg", "kgs", "kilos", "g", "grams", "l", "dl", "cl", "ml",
    "pcs", "pc", "packs", "pack", "bags", "bag", "cases", "case", "boxes", "box", "bottles",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "today", "tomorrow",
    "grated", "raaste", "raasteena", "raastetta", "raastettu", "raastettua", "raastettuna",
    "sliced", "slices", "viipale", "viipaleena", "viipaleina", "viipaleita", "viipaloitu", "viipaloitua",
    "viipaloituna", "siivu", "siivuna", "siivuina", "siivuja", "siivutettu", "siivutettua",
    "block", "blocks", "palana", "paloina", "kimpale", "kimpaleena", "kimpaleina", "kimpaleita", "tahko", "tahkona",
    "shredded", "strips", "strip", "suikale", "suikaleena", "suikaleina", "suikaleita", "suikaloitu", "suikaloitua",
    "cubed", "cubes", "diced", "kuutio", "kuutiona", "kuutioina", "kuutioita", "kuutioitu", "kuutioitua",
    "kokonainen", "kokonaisena", "kokonaisia", "kokonaiset", "kokonaista", "kokonaisen",
    "powder", "powdered", "jauhe", "jauheena", "jauhetta",
})

# T87: Product form is a hard requirement when stated. A product in another form is never an exact match:
# it is excluded or at most offered as an AGENT_SUBSTITUTE (ASK), never AUTO.


@dataclass(frozen=True)
class ProductForm:
    form_id: str
    label: str
    query_patterns: tuple[re.Pattern[str], ...]
    product_patterns: tuple[re.Pattern[str], ...]
    category_prefixes: tuple[str, ...] = ()
    category_exclude_terms: tuple[str, ...] = ()


def _compile_patterns(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


PRODUCT_FORMS: tuple[ProductForm, ...] = (
    ProductForm(
        form_id="grated",
        label="Grated",
        query_patterns=_compile_patterns(
            r"\bgrated\b",
            r"\b(?:tuore)?raaste(?:ena|tta|en|et|ita|eseen|ella|elta|essa|eksi)?\b",
            r"\braastet(?:tu|tua|tun|tuja|tuina|tuna|ut)?\b",
            r"\b[^\W\d_]+-?raaste(?:ena|tta|en|et|ita|eseen)?\b",
            r"\briven\b|\brivet\b|\brivna\b|\b[^\W\d_]*riven\b",
        ),
        product_patterns=_compile_patterns(
            r"\bgrated?\b|\bgrat\b",
            r"\b\w*raaste\w*|\b\w*raastet\w*|\braast\b",
            r"\b\w*riven\w*|\brivet\b|\brivna\b",
        ),
        category_prefixes=("10603",),
        category_exclude_terms=(
            "cottage", "raejuusto", "rae ", "keso", "crumble", "muru", "murennet",
            "cube", "kuutio", "flake", "lastu", "shav", "scaglie", "granelli", "slice", "viipale",
        ),
    ),
    ProductForm(
        form_id="sliced",
        label="Sliced",
        query_patterns=_compile_patterns(
            r"\bsliced\b|\bslices\b|\bin\s+slices\b",
            r"\bviipale(?:ena|ina|ita|et|en|eseen|ella|eksi)?\b",
            r"\bviipaloi(?:tu|tua|tun|tuja|tuna|dut)?\b",
            r"\bsiivu(?:na|ina|ja|t|n)?\b|\bsiivutet(?:tu|tua|tun|tuja|tuna)?\b",
            r"\b[^\W\d_]+-?(?:viipale|siivu)(?:ena|ina|ita|et|en|ja|t|n)?\b",
            r"\bviipale-?[^\W\d_]+\b",
            r"\bskivad\b|\bskivat\b|\bskivor\b|\b[^\W\d_]+skivor\b",
        ),
        product_patterns=_compile_patterns(
            r"\bsliced\b|\bslices\b|\bslice\b|\bsalmonslice\w*",
            r"\b\w*viipale\w*|\b\w*viipaloi\w*|\b\w*siivu\w*",
            r"\b\w*skivad\w*|\bskivat\b|\b\w*skivor\w*",
        ),
        category_prefixes=("10602",),
    ),
    ProductForm(
        form_id="block",
        label="Block",
        query_patterns=_compile_patterns(
            r"\bblocks?\b|\bin\s+a?\s*blocks?\b|\bloaf\b",
            r"\bpalana\b|\bpaloina\b|\bpalajuust\w*\b|\bjuustopala\w*\b",
            r"\bkimpale(?:ena|ina|ita|et|en)?\b|\btahko(?:na|ja|t|n)?\b",
            r"\b[^\W\d_]+-?kimpale(?:ena|ina|ita|et|en)?\b",
            r"\bhelbit\b",
        ),
        product_patterns=_compile_patterns(
            r"\bblocks?\b|\bloaf\b|\blog\b",
            r"\b\w*kimpale\w*|\bpalana\b|\bpalajuust\w*|\bjuustopala\w*|\b\w*tahko\w*",
            r"\bhelbit\b",
        ),
        category_prefixes=("10600",),
        category_exclude_terms=(
            "grated", "raaste", "sliced", "slices", "viipale", "cube", "kuutio",
            "shredded", "suikale", "crumble", "muru", "bit", "pala ",
        ),
    ),
    ProductForm(
        form_id="shredded",
        label="Shredded",
        query_patterns=_compile_patterns(
            r"\bshredded\b|\bstrips?\b|\bin\s+strips\b",
            r"\bsuikale(?:ena|ina|ita|et|en|eseen|ella)?\b",
            r"\bsuikaloi(?:tu|tua|tun|tuja|tuna|dut)?\b",
            r"\b[^\W\d_]+-?suikale(?:ena|ina|ita|et|en)?\b",
            r"\bstrimlor\b|\bstrimlad\b|\bstrimlat\b|\b[^\W\d_]+strimlor\b",
        ),
        product_patterns=_compile_patterns(
            r"\bshredded\b|\bstrips?\b",
            r"\b\w*suikale\w*|\b\w*suikaloi\w*",
            r"\b\w*striml\w*",
        ),
    ),
    ProductForm(
        form_id="cubed",
        label="Cubed",
        query_patterns=_compile_patterns(
            r"\bcubed\b|\bcubes\b|\bdiced\b|\bin\s+cubes\b",
            r"\bkuutio(?:na|ina|ita|t|n|iksi)?\b",
            r"\bkuutioi(?:tu|tua|tun|tuja|tuna|dut)?\b",
            r"\b[^\W\d_]+-?kuutio(?:na|ina|ita|t|n)?\b",
            r"\btärnad\b|\btärnat\b|\btärningar\b|\bkuber\b",
        ),
        product_patterns=_compile_patterns(
            r"\bcubed?\b|\bcubes\b|\bdiced?\b",
            r"\b\w*kuutio\w*",
            r"\b\w*tärna\w*|\bkuber\b",
        ),
    ),
    ProductForm(
        form_id="whole",
        label="Whole",
        query_patterns=_compile_patterns(
            r"\bwhole\b(?!\s*(?:milk|grain|wheat|meal|oat|rye|barley|spelt|corn|bean|beans|peeled))",
            r"\bkokonai(?:nen|sena|sia|set|sta|sen|siksi)\b",
            r"\bhela\b|\bhel\b(?!\s*mjölk)",
        ),
        product_patterns=_compile_patterns(
            r"\bwhole\b(?!\s*(?:milk|grain|wheat|meal|oat|rye|barley|spelt|corn|bean|beans|peeled))",
            r"\b\w*kokonai\w*|\bfull\s+about\b",
            r"\bhela\b|\bhel\b(?!\s*mjölk)",
        ),
    ),
    ProductForm(
        form_id="powder",
        label="Powder",
        query_patterns=_compile_patterns(
            r"\bpowder(?:ed)?\b",
            r"\bjauhe(?:ena|tta|en|et|ita|eseen|eksi)?\b",
            r"\b(?!jauhelih)[^\W\d_]+-?jauhe(?:ena|tta|en|et|ita|eseen|eksi)?\b",
            r"\bpulver\b|\b[^\W\d_]+pulver\b",
        ),
        product_patterns=_compile_patterns(
            r"\bpowder(?:ed)?\b",
            r"\b(?!jauhelih)\w*jauhe(?:ena|tta|en|et|ita)?\b",
            r"\b\w*pulver\w*",
        ),
        category_prefixes=("10405", "21394"),
    ),
)

FORMS_BY_ID: dict[str, ProductForm] = {f.form_id: f for f in PRODUCT_FORMS}
FORMS_BY_LABEL: dict[str, ProductForm] = {f.label.lower(): f for f in PRODUCT_FORMS}


def extract_requested_form(text: str = "", preferences: Sequence[Preference] = ()) -> ProductForm | None:
    """Return the first stated product form from `preferences` or `text` (EN/FI/SV), or None."""
    for pref in preferences:
        if pref.kind == "OTHER":
            key = pref.value.strip().lower()
            if key in FORMS_BY_ID:
                return FORMS_BY_ID[key]
            if key in FORMS_BY_LABEL:
                return FORMS_BY_LABEL[key]
    if not text:
        return None
    hits: list[tuple[int, int, ProductForm]] = []
    for order, form in enumerate(PRODUCT_FORMS):
        starts = [m.start() for pat in form.query_patterns for m in pat.finditer(text)]
        if starts:
            hits.append((min(starts), order, form))
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits[0][2]


def product_forms(product_names: Sequence[str], category: str = "") -> frozenset[str]:
    """Return all product forms proven by `product_names` (EN/FI/SV) and `category`."""
    text = " " + " ".join(n for n in product_names if n).lower() + " "
    cat = (category or "").strip()
    found: set[str] = set()
    for form in PRODUCT_FORMS:
        if any(pat.search(text) for pat in form.product_patterns):
            found.add(form.form_id)
    for form in PRODUCT_FORMS:
        if form.form_id in found or not form.category_prefixes:
            continue
        if any(cat.startswith(pref) for pref in form.category_prefixes):
            if any(bad in text for bad in form.category_exclude_terms):
                continue
            if form.form_id == "block" and found:
                continue
            found.add(form.form_id)
    return frozenset(found)


def product_form_difference(
    product_names: Sequence[str],
    category: str,
    requested_form: ProductForm | None,
) -> str | None:
    """Return None when `product` proves `requested_form`, else a plain-language difference string."""
    if requested_form is None:
        return None
    forms = product_forms(product_names, category)
    if requested_form.form_id in forms:
        return None
    for other in PRODUCT_FORMS:
        if other.form_id != requested_form.form_id and other.form_id in forms:
            return f"{other.form_id}, not {requested_form.form_id}"
    return f"not {requested_form.form_id}"


_SIMILAR_ALT_RE = re.compile(
    r"\b(?:or\s+(?:something\s+)?(?:similar|equivalent|comparable)|or\s+an?\s+(?:equivalent|alternative)|"
    r"tai\s+(?:muu\s+|muuta\s+|jotain\s+)?vastaava\w*)\b",
    re.IGNORECASE,
)
_ALT_CONJUNCTION_RE = re.compile(r"\s+\b(?:or|tai)\b\s+", re.IGNORECASE)


def split_query_alternatives(query: str) -> tuple[str, ...]:
    """Split an 'X or Y' / 'X tai Y' item query into acceptable alternative queries (T87)."""
    cleaned = _SIMILAR_ALT_RE.sub(" ", query or "").strip()
    if not cleaned or not _ALT_CONJUNCTION_RE.search(cleaned):
        return (cleaned,) if cleaned else ()
    raw_parts = [p.strip(" ,;-") for p in _ALT_CONJUNCTION_RE.split(cleaned) if p.strip(" ,;-")]
    if len(raw_parts) < 2:
        return (cleaned,)
    # Handle elliptical phrases like "Emmental or Edam cheese" / "salted or unsalted butter"
    # where the last alternative carries the shared product head noun.
    last_words = raw_parts[-1].split()
    shared_tail = last_words[-1] if len(last_words) >= 2 else None
    resolved_parts: list[str] = []
    for idx, part in enumerate(raw_parts):
        if (
            idx < len(raw_parts) - 1
            and shared_tail is not None
            and len(part.split()) == 1
            and not resolve_query_concepts(part)
            and shared_tail.lower() not in part.lower()
        ):
            resolved_parts.append(f"{part} {shared_tail}")
        else:
            resolved_parts.append(part)
    return tuple(resolved_parts)

_WORD_SPLIT_RE = re.compile(r"[^\wåäöÅÄÖ%-]+", re.UNICODE)
_QUANTITY_STRIP_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:kg|kgs|kilo\w*|g|grams?|l|ltr|litr\w*|dl|cl|ml|pcs?|packs?|bags?|cases?|boxes|kpl|pussi\w*|laatikko\w*|paketti\w*)\b",
    re.IGNORECASE,
)


def normalise_finnish_token(token: str) -> str:
    """Strip common Finnish/Swedish inflectional endings for compound/substring matching."""
    t = token.lower().strip()
    if len(t) <= 4:
        return t
    for suffix in ("lla", "llä", "lta", "ltä", "sta", "stä", "ssa", "ssä", "ksi", "itta", "ittä", "ineen"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 4:
            t = t[: -len(suffix)]
            break
    if t.endswith(("aa", "ää")) and not t.endswith(("maa", "pää")) and len(t) >= 6:
        t = t[:-1]
    elif t.endswith(("ta", "tä", "na", "nä")) and len(t) >= 6:
        t = t[:-2]
    elif t.endswith(("n", "t")) and len(t) >= 5:
        t = t[:-1]
    if t.startswith("kevytmaido"):
        t = "kevytmaito" + t[len("kevytmaido") :]
    elif t.startswith("maido"):
        t = "maito" + t[len("maido") :]
    return t


def extract_inline_requirements(query: str) -> tuple[str, list[Requirement]]:
    """Extract inline dietary/allergen requirements from a query string and return cleaned query text."""
    cleaned = query
    extracted: list[Requirement] = []
    for pattern, req in INLINE_REQUIREMENT_PATTERNS:
        if pattern.search(cleaned):
            if req not in extracted:
                extracted.append(req)
            cleaned = pattern.sub(" ", cleaned)
    cleaned = _QUANTITY_STRIP_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, extracted


# T89: catalogue categories in which every product is of the concept's type, so a generic request ("cheese",
# "pasta") accepts all of them even when the name only says "Edam 17 % e1 kg slices" or a brand.
TYPE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "cheese": ("106",),
    "pasta": ("21380",),
    "noodles": ("21381",),
    "rice": ("21340",),
    "yoghurt": ("10412", "10413"),
    "quark": ("10430",),
    "sour_cream": ("10418", "10419"),
}


def match_concept_in_text(
    concept: ProductConcept, text_lower: str, words: frozenset[str], category: str = ""
) -> bool:
    """Return True if a product's combined multilingual text (or its catalogue category) matches the concept."""
    for bad in concept.exclude_stems:
        if bad in text_lower:
            return False
    if category and any(category.startswith(prefix) for prefix in TYPE_CATEGORIES.get(concept.concept_id, ())):
        return True

    for en_term in concept.en_terms:
        if " " in en_term:
            if en_term in text_lower:
                return True
            parts = en_term.split()
            if all(p in words or (len(p) >= 5 and p in text_lower) for p in parts):
                return True
        else:
            if en_term in words or f"{en_term}s" in words or (len(en_term) >= 5 and en_term in text_lower):
                return True

    for stem in (*concept.fi_stems, *concept.sv_stems):
        if len(stem) >= 5:
            # Substring matching for Finnish/Swedish compounds (e.g. "tuorekaurajuoma" contains "kaurajuom").
            if stem in text_lower:
                return True
        else:
            if stem in words or any(w.startswith(stem) or w.endswith(stem) for w in words):
                return True
    return False


def _named_by_longer_phrase(concept: ProductConcept, q_lower: str, norm_text: str) -> bool:
    """True when the query names a longer phrase built on the concept's own word, which the concept excludes:
    "cream cheese" / "kermajuusto" is not cream, "peanut butter" is not butter (T74: the longest match wins)."""
    terms = (*concept.en_terms, *concept.fi_stems, *concept.sv_stems)
    for bad in concept.exclude_stems:
        if (bad in q_lower or bad in norm_text) and any(len(t) >= 3 and t in bad for t in terms):
            return True
    return False


def resolve_query_concepts(cleaned_query: str) -> list[ProductConcept]:
    """Resolve a cleaned query string (EN/FI/SV) into ordered matching ProductConcepts."""
    q_lower = cleaned_query.lower().strip()
    if not q_lower:
        return []
    raw_words = [w for w in _WORD_SPLIT_RE.split(q_lower) if w and w not in QUERY_STOPWORDS]
    norm_words = [normalise_finnish_token(w) for w in raw_words]
    word_set = frozenset(raw_words) | frozenset(norm_words)
    norm_text = " ".join(norm_words)

    scored: list[tuple[int, int, ProductConcept]] = []
    for order, concept in enumerate(PRODUCT_CONCEPTS):
        if _named_by_longer_phrase(concept, q_lower, norm_text):
            continue
        # The longest term that matched: "cream cheese" (12) outranks "cream" (5), "maitojauhe" outranks "maito".
        best = 0
        for en_term in concept.en_terms:
            if " " in en_term:
                if en_term in q_lower or all(p in word_set for p in en_term.split()):
                    best = max(best, len(en_term))
            elif en_term in word_set:
                best = max(best, len(en_term))
        for stem in (*concept.fi_stems, *concept.sv_stems):
            if len(stem) >= 5:
                if stem in q_lower or stem in norm_text:
                    best = max(best, len(stem))
            elif stem in word_set or (
                # A two-letter stem ("te") is only ever a whole word: "levite" is not tea.
                len(stem) >= 3 and any(w.startswith(stem) or w.endswith(stem) for w in norm_words)
            ):
                best = max(best, len(stem))
        if best:
            scored.append((-best, order, concept))
    matched = [concept for _best, _order, concept in sorted(scored, key=lambda hit: (hit[0], hit[1]))]

    # Prefer specific concepts over generic ones (e.g. oat_drink over milk, macaroni over pasta).
    specific_ids = {c.concept_id for c in matched}
    if "oat_drink" in specific_ids or "soy_drink" in specific_ids or "almond_drink" in specific_ids or "coconut_milk" in specific_ids:
        matched = [c for c in matched if c.concept_id != "milk"]
    if any(c.parent_concept == "pasta" for c in matched):
        matched = [c for c in matched if c.concept_id != "pasta"] + [c for c in matched if c.concept_id == "pasta"]
    return matched


def expand_query_tokens(cleaned_query: str) -> tuple[list[tuple[str, ...]], frozenset[str]]:
    """Expand remaining query tokens into multilingual synonym groups for free-text matching."""
    q_lower = cleaned_query.lower().strip()
    raw_words = [
        w
        for w in _WORD_SPLIT_RE.split(q_lower)
        if w and w not in QUERY_STOPWORDS and not w.isdigit()
    ]
    groups: list[tuple[str, ...]] = []
    all_terms: set[str] = set()
    for word in raw_words:
        norm = normalise_finnish_token(word)
        synonyms: list[str] = [word]
        if norm != word:
            synonyms.append(norm)
        for stem, en_syns in FI_SV_TO_EN_SYNONYMS:
            if stem == word or stem == norm or (len(stem) >= 5 and (stem in word or stem in norm)):
                for s in en_syns:
                    if s not in synonyms:
                        synonyms.append(s)
        group = tuple(synonyms)
        groups.append(group)
        all_terms.update(group)
    return groups, frozenset(all_terms)
