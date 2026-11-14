"""Food/non-food classification and per-category profiles for product-type matching."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.contracts.common import Contract, RequestUnit


class CategoryProfile(Contract):
    codePrefix: str
    productType: str
    isFood: bool
    defaultUnit: RequestUnit = "kg"
    keywordsEn: tuple[str, ...] = ()
    keywordsFi: tuple[str, ...] = ()
    keywordsSv: tuple[str, ...] = ()


FOOD_PREFIXES_3: frozenset[str] = frozenset({
    "104", "105", "106", "109", "110", "172", "173", "174", "187",
    "211", "212", "213", "214", "215", "216", "219", "221", "223",
    "224", "225", "226", "227", "417", "418", "522", "849", "850",
})

NON_FOOD_PREFIXES_3: frozenset[str] = frozenset({
    "200", "300", "329", "630", "631", "632", "633", "639",
    "640", "641", "643", "645", "677", "678", "777", "900",
})

NON_FOOD_KEYWORDS: frozenset[str] = frozenset({
    "non-food", "non_food", "nonfood", "cleaning", "packaging", "tableware",
    "disposable", "disposables", "equipment", "office", "hygiene", "chemical",
    "chemicals", "workwear", "appliance", "appliances", "napkin", "napkins",
})

FOOD_KEYWORDS: frozenset[str] = frozenset({
    "food", "dairy", "milk", "milk_drink", "oat_drink", "plant_drink", "cream",
    "yoghurt", "cheese", "bread", "bakery", "pastry", "meat", "poultry", "fish",
    "seafood", "produce", "vegetable", "fruit", "pasta", "macaroni", "noodles",
    "rice", "flour", "grains", "cereals", "flakes", "pulses", "pantry", "sauce",
    "soup", "spices", "coffee", "tea", "beverage", "beverages", "frozen",
    "ice_cream", "confectionery", "synthetic",
})

CATEGORY_PROFILES_5: Mapping[str, CategoryProfile] = MappingProxyType({
    "10400": CategoryProfile(
        codePrefix="10400",
        productType="milk",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("milk", "milk drink", "skimmed milk", "semi skimmed milk", "whole milk"),
        keywordsFi=("maito", "maitojuoma", "kevytmaito", "kevytmaitojuoma", "rasvaton maito", "täysmaito"),
        keywordsSv=("mjölk", "mjölkdryck", "lättmjölk", "lättmjölksdryck", "skummjölk", "helmjölk"),
    ),
    "10401": CategoryProfile(
        codePrefix="10401",
        productType="cream",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("cream", "whipping cream", "cooking cream", "coffee cream"),
        keywordsFi=("kerma", "kuohukerma", "ruokakerma", "kahvikerma"),
        keywordsSv=("grädde", "vispgrädde", "matgrädde", "kaffegrädde"),
    ),
    "10402": CategoryProfile(
        codePrefix="10402",
        productType="plant_drink",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("oat drink", "oat milk", "soy drink", "soya drink", "almond drink", "coconut milk", "plant drink"),
        keywordsFi=("kaurajuoma", "kauramaito", "soijajuoma", "mantelijuoma", "kookosmaito", "kasvijuoma"),
        keywordsSv=("havredryck", "sojadryck", "mandeldryck", "kokosmjölk", "växtdryck"),
    ),
    "10404": CategoryProfile(
        codePrefix="10404",
        productType="condensed_milk",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("condensed milk",),
        keywordsFi=("kondensoitu maito", "makeutettu maitotiiviste"),
        keywordsSv=("kondenserad mjölk",),
    ),
    "10405": CategoryProfile(
        codePrefix="10405",
        productType="milk_powder",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("milk powder", "skimmed milk powder", "cream powder"),
        keywordsFi=("maitojauhe", "rasvaton maitojauhe", "kermajauhe"),
        keywordsSv=("mjölkpulver", "skummjölkspulver", "gräddpulver"),
    ),
    "10406": CategoryProfile(
        codePrefix="10406",
        productType="plant_cream",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("oat cooking", "plant cream", "coconut cream", "cooking cream"),
        keywordsFi=("kaurakerma", "kasvirasvasekoite", "kookoskerma"),
        keywordsSv=("havregrädde", "kokosgrädde"),
    ),
    "10411": CategoryProfile(
        codePrefix="10411",
        productType="fermented_milk",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("fermented milk", "buttermilk", "curdled milk", "viili"),
        keywordsFi=("piimä", "viili"),
        keywordsSv=("filmjölk", "surmjölk"),
    ),
    "10412": CategoryProfile(
        codePrefix="10412",
        productType="yoghurt",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("yoghurt", "yogurt", "turkish yoghurt", "greek yoghurt"),
        keywordsFi=("jogurtti", "turkkilainen jogurtti", "kreikkalainen jogurtti"),
        keywordsSv=("yoghurt", "turkisk yoghurt", "grekisk yoghurt"),
    ),
    "10413": CategoryProfile(
        codePrefix="10413",
        productType="plant_yoghurt",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("soy yoghurt", "oat snack", "fermented soya", "plant dessert"),
        keywordsFi=("soijajogurtti", "kauravälipala", "kasvipohjainen välipala"),
        keywordsSv=("sojayoghurt", "havremellanmål"),
    ),
    "10418": CategoryProfile(
        codePrefix="10418",
        productType="plant_fraiche",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("plant fraiche", "oat fraiche"),
        keywordsFi=("kaurafraiche", "kasvifraiche"),
        keywordsSv=("havrefraiche",),
    ),
    "10419": CategoryProfile(
        codePrefix="10419",
        productType="sour_cream",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("sour cream", "creme fraiche", "crème fraîche", "kefir"),
        keywordsFi=("kermaviili", "ranskankerma", "smetana", "kefiiri"),
        keywordsSv=("gräddfil", "crème fraiche", "smetana", "kefir"),
    ),
    "10430": CategoryProfile(
        codePrefix="10430",
        productType="quark",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("quark", "curd"),
        keywordsFi=("rahka", "maitorahka"),
        keywordsSv=("kvarg",),
    ),
    "21300": CategoryProfile(
        codePrefix="21300",
        productType="flour",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("flour", "wheat flour"),
        keywordsFi=("jauho", "vehnäjauho"),
        keywordsSv=("mjöl", "vetemjöl"),
    ),
    "21310": CategoryProfile(
        codePrefix="21310",
        productType="flour_breadcrumbs",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("flour", "breadcrumbs", "spelt flour", "wholegrain flour"),
        keywordsFi=("korppujauho", "spelttijauho", "täysjyväjauho"),
        keywordsSv=("ströbröd", "speltmjöl", "fullkornsmjöl"),
    ),
    "21320": CategoryProfile(
        codePrefix="21320",
        productType="flour_mix",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("flour mix", "bread flour mix", "gluten free flour"),
        keywordsFi=("jauhoseos", "gluteeniton jauhoseos"),
        keywordsSv=("mjölmix", "glutenfri mjölmix"),
    ),
    "21340": CategoryProfile(
        codePrefix="21340",
        productType="rice",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("rice", "risotto rice", "basmati rice", "jasmine rice"),
        keywordsFi=("riisi", "risottoriisi", "basmatiriisi", "jasmiiniriisi"),
        keywordsSv=("ris", "risottoris", "basmatiris", "jasminris"),
    ),
    "21360": CategoryProfile(
        codePrefix="21360",
        productType="starch",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("cornstarch", "corn starch", "potato starch", "roux", "thickening"),
        keywordsFi=("maissitärkkelys", "perunajauho", "suuruste"),
        keywordsSv=("majsstärkelse", "potatismjöl", "redning"),
    ),
    "21370": CategoryProfile(
        codePrefix="21370",
        productType="flakes",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("flakes", "oat flakes", "oatmeal", "rolled oats", "barley flakes"),
        keywordsFi=("hiutale", "kaurahiutale", "ohrahiutale", "puurohiutale"),
        keywordsSv=("flingor", "havreflingor", "havregryn", "kornflingor"),
    ),
    "21371": CategoryProfile(
        codePrefix="21371",
        productType="grits_groats",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("semolina", "barley grain", "grits", "groats"),
        keywordsFi=("mannasuurimo", "ohrasuurimo", "suurimo"),
        keywordsSv=("mannagryn", "korngryn", "gryn"),
    ),
    "21372": CategoryProfile(
        codePrefix="21372",
        productType="cereals",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("cereals", "cornflakes", "breakfast cereal"),
        keywordsFi=("murot", "maissihiutaleet", "aamiaismurot"),
        keywordsSv=("flingor", "frukostflingor", "cornflakes"),
    ),
    "21373": CategoryProfile(
        codePrefix="21373",
        productType="muesli_granola",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("muesli", "müesli", "granola"),
        keywordsFi=("mysli", "granola"),
        keywordsSv=("müsli", "granola"),
    ),
    "21380": CategoryProfile(
        codePrefix="21380",
        productType="pasta",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("pasta", "macaroni", "maccheroni", "fusilli", "penne", "spaghetti", "lasagne", "tagliatelle"),
        keywordsFi=("pasta", "makaroni", "fusilli", "penne", "spagetti", "lasagne"),
        keywordsSv=("pasta", "makaroner", "fusilli", "penne", "spaghetti", "lasagne"),
    ),
    "21381": CategoryProfile(
        codePrefix="21381",
        productType="noodles",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("noodles", "egg noodles", "rice noodles", "udon"),
        keywordsFi=("nuudeli", "nuudelit", "munanuudeli", "riisinuudeli"),
        keywordsSv=("nudlar", "äggnudlar", "risnudlar"),
    ),
    "21382": CategoryProfile(
        codePrefix="21382",
        productType="grains",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("quinoa", "couscous", "bulgur"),
        keywordsFi=("kvinoa", "kuskus", "couscous", "bulgur"),
        keywordsSv=("quinoa", "couscous", "bulgur"),
    ),
    "21390": CategoryProfile(
        codePrefix="21390",
        productType="pulses",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("beans", "lentils", "chickpeas", "peas"),
        keywordsFi=("pavut", "linssit", "kikherneet", "herneet"),
        keywordsSv=("bönor", "linser", "kikärtor", "ärtor"),
    ),
    "21393": CategoryProfile(
        codePrefix="21393",
        productType="plant_protein",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("soy mince", "pea protein", "soy strips"),
        keywordsFi=("soijarouhe", "herneproteiini", "soijasuikale"),
        keywordsSv=("sojafärs", "ärtprotein", "sojastrimlor"),
    ),
    "21394": CategoryProfile(
        codePrefix="21394",
        productType="potato_flakes",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("potato flakes", "mashed potato powder", "potato granules"),
        keywordsFi=("perunahiutale", "perunamuusijauhe"),
        keywordsSv=("potatisflingor", "potatismos"),
    ),
})

CATEGORY_PROFILES_3: Mapping[str, CategoryProfile] = MappingProxyType({
    "104": CategoryProfile(
        codePrefix="104",
        productType="dairy_and_plant_dairy",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("milk", "milk drink", "oat drink", "cream", "yoghurt", "quark", "sour cream", "dairy"),
        keywordsFi=("maito", "maitojuoma", "kaurajuoma", "kerma", "jogurtti", "rahka", "kermaviili"),
        keywordsSv=("mjölk", "mjölkdryck", "havredryck", "grädde", "yoghurt", "kvarg", "gräddfil"),
    ),
    "105": CategoryProfile(
        codePrefix="105",
        productType="fats_and_oils",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("butter", "margarine", "oil", "rapeseed oil", "olive oil"),
        keywordsFi=("voi", "margariini", "öljy", "rypsiöljy", "oliiviöljy"),
        keywordsSv=("smör", "margarin", "olja", "rapsolja", "olivolja"),
    ),
    "106": CategoryProfile(
        codePrefix="106",
        productType="cheese",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("cheese", "cheddar", "mozzarella", "feta", "emmental", "cream cheese"),
        keywordsFi=("juusto", "emmental", "mozzarella", "tuorejuusto", "salaattijuusto"),
        keywordsSv=("ost", "mozzarella", "färskost", "salladsost"),
    ),
    "109": CategoryProfile(
        codePrefix="109",
        productType="bread",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("bread", "rye bread", "loaf", "rolls"),
        keywordsFi=("leipä", "ruisleipä", "sämpylä", "paahtoleipä"),
        keywordsSv=("bröd", "rågbröd", "fralla", "rostbröd"),
    ),
    "110": CategoryProfile(
        codePrefix="110",
        productType="sweet_bakery",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("cake", "bun", "pastry", "sweet loaf"),
        keywordsFi=("pulla", "kakku", "pitko", "leivos"),
        keywordsSv=("bulle", "kaka", "bakelse"),
    ),
    "172": CategoryProfile(
        codePrefix="172",
        productType="meat_charcuterie",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("sausage", "ham", "salami", "bacon", "cold cuts", "meat"),
        keywordsFi=("makkara", "kinkku", "salami", "pekoni", "leikkele", "liha"),
        keywordsSv=("korv", "skinka", "salami", "bacon", "pålägg", "kött"),
    ),
    "173": CategoryProfile(
        codePrefix="173",
        productType="ready_meals_deli",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("salad", "potato salad", "casserole", "ready meal", "soup"),
        keywordsFi=("salaatti", "perunasalaatti", "laatikko", "valmisruoka", "keitto"),
        keywordsSv=("sallad", "potatissallad", "låda", "färdigmat", "soppa"),
    ),
    "174": CategoryProfile(
        codePrefix="174",
        productType="fresh_produce",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("vegetable", "fruit", "potato", "tomato", "cucumber", "mushroom", "onion", "apple"),
        keywordsFi=("vihannes", "hedelmä", "peruna", "tomaatti", "kurkku", "sieni", "sipuli", "omena"),
        keywordsSv=("grönsak", "frukt", "potatis", "tomat", "gurka", "svamp", "lök", "äpple"),
    ),
    "187": CategoryProfile(
        codePrefix="187",
        productType="fish_and_poultry",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("fish", "salmon", "herring", "chicken", "turkey", "poultry", "shrimp"),
        keywordsFi=("kala", "lohi", "silli", "broileri", "kana", "kalkkuna", "katkarapu"),
        keywordsSv=("fisk", "lax", "sill", "kyckling", "kalkon", "räkor"),
    ),
    "211": CategoryProfile(
        codePrefix="211",
        productType="biscuits_crackers",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("biscuit", "cookie", "cracker", "wafer"),
        keywordsFi=("keksi", "pipari", "suolakeksi", "vohveli"),
        keywordsSv=("kex", "pepparkaka", "rån"),
    ),
    "212": CategoryProfile(
        codePrefix="212",
        productType="crispbread",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("crispbread", "rye crispbread"),
        keywordsFi=("näkkileipä", "hapankorppu"),
        keywordsSv=("knäckebröd", "surskorpa"),
    ),
    "213": CategoryProfile(
        codePrefix="213",
        productType="grains_pasta_flour",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("pasta", "macaroni", "fusilli", "penne", "spaghetti", "rice", "flour", "flakes", "oats"),
        keywordsFi=("pasta", "makaroni", "fusilli", "penne", "spagetti", "riisi", "jauho", "hiutale"),
        keywordsSv=("pasta", "makaroner", "fusilli", "penne", "spaghetti", "ris", "mjöl", "flingor"),
    ),
    "214": CategoryProfile(
        codePrefix="214",
        productType="jams_and_desserts",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("jam", "marmalade", "compote", "fruit soup", "kissel"),
        keywordsFi=("hillo", "marmeladi", "kiisseli", "marjakeitto"),
        keywordsSv=("sylt", "marmelad", "kräm", "soppa"),
    ),
    "215": CategoryProfile(
        codePrefix="215",
        productType="canned_vegetables",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("crushed tomatoes", "tomato paste", "pickles", "olives", "canned"),
        keywordsFi=("tomaattimurska", "tomaattipyree", "suolakurkku", "oliivi", "säilyke"),
        keywordsSv=("krossade tomater", "tomatpuré", "ättiksgurka", "oliver", "konserv"),
    ),
    "216": CategoryProfile(
        codePrefix="216",
        productType="baby_food",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("baby food", "fruit puree"),
        keywordsFi=("lastenruoka", "hedelmäsose"),
        keywordsSv=("barnmat", "fruktpuré"),
    ),
    "219": CategoryProfile(
        codePrefix="219",
        productType="sauces_soups_snacks",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("sauce", "broth", "stock", "soup", "mayonnaise", "ketchup", "chips"),
        keywordsFi=("kastike", "liemi", "keitto", "majoneesi", "ketsuppi", "sipsit"),
        keywordsSv=("sås", "buljong", "soppa", "majonnäs", "ketchup", "chips"),
    ),
    "221": CategoryProfile(
        codePrefix="221",
        productType="sugar_and_sweeteners",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("sugar", "syrup", "honey", "sweetener"),
        keywordsFi=("sokeri", "siirappi", "hunaja", "makeutusaine"),
        keywordsSv=("socker", "sirap", "honung", "sötningsmedel"),
    ),
    "223": CategoryProfile(
        codePrefix="223",
        productType="ground_coffee",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("coffee", "ground coffee", "filter coffee"),
        keywordsFi=("kahvi", "suodatinkahvi", "pannukahvi"),
        keywordsSv=("kaffe", "bryggkaffe", "pannkaffe"),
    ),
    "224": CategoryProfile(
        codePrefix="224",
        productType="coffee_beans_instant",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("coffee beans", "instant coffee", "espresso"),
        keywordsFi=("kahvipavut", "pikakahvi", "espresso"),
        keywordsSv=("kaffebönor", "snabbkaffe", "espresso"),
    ),
    "225": CategoryProfile(
        codePrefix="225",
        productType="tea_and_cocoa",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("tea", "cocoa", "hot chocolate"),
        keywordsFi=("tee", "kaakao", "kaakaojuoma"),
        keywordsSv=("te", "kakao", "chokladdryck"),
    ),
    "226": CategoryProfile(
        codePrefix="226",
        productType="spices_and_baking",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("salt", "pepper", "spice", "mustard", "vinegar", "yeast", "baking powder"),
        keywordsFi=("suola", "pippuri", "mauste", "sinappi", "etikka", "hiiva", "leivinjauhe"),
        keywordsSv=("salt", "peppar", "krydda", "senap", "ättika", "jäst", "bakpulver"),
    ),
    "227": CategoryProfile(
        codePrefix="227",
        productType="soft_drinks_juices_water",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("juice", "soft drink", "mineral water", "lemonade", "water"),
        keywordsFi=("mehu", "virvoitusjuoma", "kivennäisvesi", "lähdevesi"),
        keywordsSv=("juice", "läsk", "mineralvatten", "källvatten"),
    ),
    "417": CategoryProfile(
        codePrefix="417",
        productType="frozen_food",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("frozen", "frozen vegetables", "frozen berries", "french fries"),
        keywordsFi=("pakaste", "pakastevihannekset", "pakastemarjat", "ranskanperunat"),
        keywordsSv=("fryst", "frysta grönsaker", "frysta bär", "pommes frites"),
    ),
    "418": CategoryProfile(
        codePrefix="418",
        productType="ice_cream_frozen_desserts",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("ice cream", "sorbet", "pancakes", "crepes"),
        keywordsFi=("jäätelö", "sorbetti", "ohukaiset", "räiskäleet"),
        keywordsSv=("glass", "sorbet", "pannkakor", "plättar"),
    ),
    "522": CategoryProfile(
        codePrefix="522",
        productType="confectionery",
        isFood=True,
        defaultUnit="kg",
        keywordsEn=("chocolate", "candy", "sweets", "licorice", "pastilles"),
        keywordsFi=("suklaa", "makeiset", "karkki", "lakritsi", "pastilli"),
        keywordsSv=("choklad", "godis", "lakrits", "pastiller"),
    ),
    "849": CategoryProfile(
        codePrefix="849",
        productType="non_alcoholic_cider_beer",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("alcohol free beer", "alcohol free cider"),
        keywordsFi=("alkoholiton olut", "alkoholiton siideri"),
        keywordsSv=("alkoholfri öl", "alkoholfri cider"),
    ),
    "850": CategoryProfile(
        codePrefix="850",
        productType="non_alcoholic_beverages",
        isFood=True,
        defaultUnit="l",
        keywordsEn=("non-alcoholic beer", "non-alcoholic wine", "ginger beer"),
        keywordsFi=("alkoholiton olut", "alkoholiton viini", "inkivääriolut"),
        keywordsSv=("alkoholfri öl", "alkoholfritt vin"),
    ),
    # Non-food categories
    "200": CategoryProfile(
        codePrefix="200",
        productType="tableware_kitchenware",
        isFood=False,
        defaultUnit="pcs",
        keywordsEn=("plate", "glass", "tumbler", "cutlery", "tray", "kitchenware"),
        keywordsFi=("lautanen", "lasi", "aterimet", "tarjotin", "keittiötarvike"),
        keywordsSv=("tallrik", "glas", "bestick", "bricka"),
    ),
    "300": CategoryProfile(codePrefix="300", productType="garden_pest_control", isFood=False, defaultUnit="pcs"),
    "329": CategoryProfile(codePrefix="329", productType="lighters_matches", isFood=False, defaultUnit="pcs"),
    "630": CategoryProfile(codePrefix="630", productType="personal_care_hygiene", isFood=False, defaultUnit="pcs"),
    "631": CategoryProfile(
        codePrefix="631",
        productType="cleaning_and_waste",
        isFood=False,
        defaultUnit="pcs",
        keywordsEn=("detergent", "cleaner", "disinfectant", "waste bag", "trash bag"),
        keywordsFi=("pesuaine", "puhdistusaine", "jätesäkki", "roskapussi"),
        keywordsSv=("rengöringsmedel", "diskmedel", "sopsäck"),
    ),
    "632": CategoryProfile(
        codePrefix="632",
        productType="paper_and_packaging",
        isFood=False,
        defaultUnit="pcs",
        keywordsEn=("napkin", "toilet paper", "paper towel", "foil", "clinging film", "baking paper"),
        keywordsFi=("lautasliina", "wc-paperi", "käsipyyhe", "folio", "kelmu", "leivinpaperi"),
        keywordsSv=("servett", "toalettpapper", "pappershandduk", "folie", "bakplåtspapper"),
    ),
    "633": CategoryProfile(codePrefix="633", productType="workwear", isFood=False, defaultUnit="pcs"),
    "639": CategoryProfile(codePrefix="639", productType="kitchen_appliances", isFood=False, defaultUnit="pcs"),
    "640": CategoryProfile(
        codePrefix="640",
        productType="disposable_tableware",
        isFood=False,
        defaultUnit="pcs",
        keywordsEn=("plastic cup", "paper cup", "disposable plate", "candle"),
        keywordsFi=("kertakäyttömuki", "pahvimuki", "kertakäyttölautanen", "kynttilä"),
        keywordsSv=("engångsmugg", "engångstallrik", "ljus"),
    ),
    "641": CategoryProfile(codePrefix="641", productType="pet_and_misc", isFood=False, defaultUnit="pcs"),
    "643": CategoryProfile(codePrefix="643", productType="thermometers", isFood=False, defaultUnit="pcs"),
    "645": CategoryProfile(codePrefix="645", productType="gift_packaging", isFood=False, defaultUnit="pcs"),
    "677": CategoryProfile(codePrefix="677", productType="newspapers_magazines", isFood=False, defaultUnit="pcs"),
    "678": CategoryProfile(codePrefix="678", productType="seasonal_decorations", isFood=False, defaultUnit="pcs"),
    "777": CategoryProfile(codePrefix="777", productType="industrial_gas", isFood=False, defaultUnit="kg"),
    "900": CategoryProfile(codePrefix="900", productType="office_supplies", isFood=False, defaultUnit="pcs"),
})

SYNTHETIC_CATEGORY_PROFILES: Mapping[str, CategoryProfile] = MappingProxyType({
    "dairy": CATEGORY_PROFILES_5["10400"],
    "milk": CATEGORY_PROFILES_5["10400"],
    "oat_drink": CATEGORY_PROFILES_5["10402"],
    "plant_drink": CATEGORY_PROFILES_5["10402"],
    "pasta": CATEGORY_PROFILES_5["21380"],
    "pantry": CATEGORY_PROFILES_3["213"],
    "produce": CATEGORY_PROFILES_3["174"],
    "proteins": CATEGORY_PROFILES_3["187"],
    "soups": CATEGORY_PROFILES_3["219"],
    "food": CategoryProfile(codePrefix="food", productType="food", isFood=True, defaultUnit="kg"),
    "synthetic": CategoryProfile(codePrefix="synthetic", productType="synthetic", isFood=True, defaultUnit="kg"),
    "non-food": CategoryProfile(codePrefix="non-food", productType="non_food", isFood=False, defaultUnit="pcs"),
    "non_food": CategoryProfile(codePrefix="non_food", productType="non_food", isFood=False, defaultUnit="pcs"),
    "cleaning": CATEGORY_PROFILES_3["631"],
    "packaging": CATEGORY_PROFILES_3["632"],
    "disposable": CATEGORY_PROFILES_3["640"],
    "tableware": CATEGORY_PROFILES_3["200"],
    "office": CATEGORY_PROFILES_3["900"],
})


def get_category_profile(category: str) -> CategoryProfile:
    """Resolve the most specific CategoryProfile for a Valio or synthetic category code."""
    clean = (category or "").strip()
    if clean in CATEGORY_PROFILES_5:
        return CATEGORY_PROFILES_5[clean]
    prefix3 = clean[:3]
    if prefix3 in CATEGORY_PROFILES_3:
        return CATEGORY_PROFILES_3[prefix3]
    lower = clean.lower()
    if lower in SYNTHETIC_CATEGORY_PROFILES:
        return SYNTHETIC_CATEGORY_PROFILES[lower]
    if lower in NON_FOOD_KEYWORDS or (clean and clean[0] in {"3", "6", "7", "9"}):
        return CategoryProfile(codePrefix=clean or "unknown", productType="non_food", isFood=False, defaultUnit="pcs")
    return CategoryProfile(codePrefix=clean or "unknown", productType="food", isFood=True, defaultUnit="kg")


def is_food_category(category: str, record: dict | None = None) -> bool:
    """Classify a product category (and optional raw record) as food vs non-food."""
    clean = (category or "").strip()
    prefix3 = clean[:3]
    if prefix3 in NON_FOOD_PREFIXES_3:
        return False
    if prefix3 in FOOD_PREFIXES_3:
        return True
    lower = clean.lower()
    if lower in NON_FOOD_KEYWORDS:
        return False
    if lower in FOOD_KEYWORDS:
        return True
    if clean and clean[0] in {"3", "6", "7", "9"}:
        return False
    if record:
        synkka = record.get("synkkaData") or {}
        if synkka.get("nutritionalContent"):
            return True
        groups = {g.get("name"): g.get("values") for g in synkka.get("classifications", [])}
        if groups.get("allergen") or groups.get("nonAllergen") or groups.get("nutritionalClaim"):
            return True
    return True


def matching_categories_for_type(query: str) -> tuple[str, ...]:
    """Return matching 5-digit and 3-digit category prefixes for a product-type keyword in EN/FI/SV."""
    needle = (query or "").strip().lower()
    if not needle:
        return ()
    matched: list[str] = []
    for profile in (*CATEGORY_PROFILES_5.values(), *CATEGORY_PROFILES_3.values()):
        all_terms = (profile.productType, *profile.keywordsEn, *profile.keywordsFi, *profile.keywordsSv)
        if any(needle == term.lower() or needle in term.lower() or term.lower() in needle for term in all_terms):
            if profile.codePrefix not in matched:
                matched.append(profile.codePrefix)
    return tuple(matched)


# ---- Product-type precision (T34) ------------------------------------------------------------------
# A qualifier in the request that changes how the product is used is a hard product-type constraint:
# whipping vs cooking cream, UHT vs fresh, a drink vs a cooking product, dairy vs a plant-based
# "alternative". A product that does not prove the requested type may only be offered as an agent
# substitute (AGENT_SUBSTITUTE, L10, ASK), never chosen on its own authority.


@dataclass(frozen=True)
class TypeVariant:
    variant_id: str
    label: str
    query_patterns: tuple[str, ...]  # regexes on the buyer's words (EN/FI/SV)
    product_terms: tuple[str, ...] = ()  # the product proves the variant if its names contain one of these
    forbid_terms: tuple[str, ...] = ()  # the product disproves the variant if its names contain one of these
    forbid_label: str | None = None  # how to name a disproving term that belongs to no other variant


@dataclass(frozen=True)
class TypeQualifierGroup:
    group_id: str
    concepts: frozenset[str]  # requested concepts the group applies to; empty = any
    variants: tuple[TypeVariant, ...]


_FROZEN_TERMS = ("frozen", "pakaste", "pakastet", "jäädytet", "fryst", "iqf")
_SMOKED_TERMS = ("smoked", "savu", "savustet", "rökt")
_CURED_TERMS = ("graavi", "gravad", "graved", "marinated", "cured", "lightly salted", "kevytsuolat")
# T52: salted vs unsalted is a stated attribute. "unsalted" contains "salted", so the salted terms match only
# at the start of a word (the product text is padded with a leading space).
_UNSALTED_TERMS = ("unsalted", "suolaton", "suolatont", "suolattom", "osaltat", "saltfri", "without salt")
_SALTED_TERMS = (" salted", "-salted", "suolattu", "normaalisuola", "suolainen", " saltat")
_PROCESSED_CHEESE_TERMS = (
    "processed", "sulatejuust", "sulate ", "smältost", "juustoa sisältäv", "cheese-containing", "imitation",
)
_SPREAD_TERMS = (
    "margarin", "margariin", "levite", "rasvaseos", "spread", "blend", "bregott", "oivariini", "keiju", "flora",
    "becel", "kasvivoima", "plant power",
)
# (id, label, query patterns, product terms)
_CHEESE_VARIETIES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("emmental", "Emmental", (r"\bemmental\w*", r"\bemmentaal\w*"), ("emmental", "emmentaal")),
    ("edam", "Edam", (r"\bedam\w*",), ("edam",)),
    ("gouda", "Gouda", (r"\bgouda\w*",), ("gouda",)),
    ("mozzarella", "mozzarella", (r"\bmozzarell\w*",), ("mozzarell",)),
    ("feta", "feta", (r"\bfeta\w*",), ("feta",)),
    ("cheddar", "cheddar", (r"\bcheddar\w*",), ("cheddar",)),
    ("halloumi", "halloumi", (r"\bhalloumi\w*",), ("halloumi",)),
    ("parmesan", "parmesan", (r"\bparmesa\w*", r"\bparmigiano\b"), ("parmesa", "parmigiano")),
    ("brie", "brie", (r"\bbrie\b",), ("brie",)),
    ("camembert", "camembert", (r"\bcamembert\w*",), ("camembert",)),
    ("blue", "blue cheese", (r"\bblue cheese\b", r"\bsinihome\w*", r"\bgorgonzola\b", r"\broquefort\b"),
     ("blue cheese", "sinihome", "gorgonzola", "roquefort")),
    ("fontal", "fontal", (r"\bfontal\w*",), ("fontal",)),
)

TYPE_QUALIFIER_GROUPS: tuple[TypeQualifierGroup, ...] = (
    TypeQualifierGroup(
        group_id="cream_use",
        concepts=frozenset({"cream"}),
        variants=(
            TypeVariant(
                "whipping", "whipping cream",
                (r"\bwhipp(ing|ed)\b", r"\b(vispi|kuohu)\w*", r"\bvispgrädde\w*"),
                product_terms=("whipping", "vispi", "kuohu", "vispgrädde"),
            ),
            TypeVariant(
                "cooking", "cooking cream",
                (r"\bcooking\b", r"\bruokakerm\w*", r"\bmatlagningsgrädde\w*"),
                product_terms=("cooking", "ruokakerm", "ruoka ", "matlagning"),
            ),
            TypeVariant(
                "coffee", "coffee cream",
                (r"\bcoffee\b", r"\bkahvikerm\w*", r"\bkaffegrädde\w*"),
                product_terms=("coffee", "kahvikerm", "kaffegrädde"),
            ),
            TypeVariant(
                "light", "low-fat cream",
                (r"\blow[- ]fat\b", r"\bkevytkerm\w*", r"\blättgrädde\w*"),
                product_terms=("low fat", "low-fat", "kevytkerm", "lättgrädde"),
            ),
        ),
    ),
    TypeQualifierGroup(
        group_id="heat_treatment",
        concepts=frozenset(),
        variants=(
            TypeVariant("uht", "UHT", (r"\buht\b", r"\blong[- ]life\b"), product_terms=("uht",)),
            TypeVariant(
                "fresh", "fresh (not UHT)",
                (r"\bfresh\b", r"\btuore(tta|ita|et|a)?\b", r"\bfärska?\b"),
                forbid_terms=("uht",),
            ),
        ),
    ),
    TypeQualifierGroup(
        group_id="drink_vs_cooking",
        concepts=frozenset(),
        variants=(
            TypeVariant(
                "drink", "drink",
                (r"\bdrinks?\b", r"juoma\w*\b", r"dryck\w*\b"),
                forbid_terms=("cooking", "ruoka", "matlagning", "fraiche", "yoghurt", "jogurt"),
            ),
        ),
    ),
    # T39: fresh vs frozen / smoked / cured (fish, meat, vegetables …), stated by the buyer.
    TypeQualifierGroup(
        group_id="product_state",
        concepts=frozenset(),
        variants=(
            TypeVariant(
                "fresh", "fresh",
                (r"\bfresh\b", r"\btuore(tta|ita|et|a)?\b", r"\bfärska?\b"),
                forbid_terms=(*_FROZEN_TERMS, *_SMOKED_TERMS, *_CURED_TERMS),
            ),
            TypeVariant(
                "frozen", "frozen",
                (r"\bfrozen\b", r"\bpakaste\w*", r"\bpakastet\w*", r"\bjäädytet\w*", r"\bfryst\w*", r"\biqf\b"),
                product_terms=_FROZEN_TERMS,
            ),
            TypeVariant(
                "smoked", "smoked",
                (r"\bsmoked\b", r"\w*savu\w*", r"\w*savustet\w*", r"\w*rökt\w*"),
                product_terms=_SMOKED_TERMS,
            ),
            TypeVariant(
                "cured", "cured or marinated",
                (r"\bgraavi\w*", r"\bgravad\w*", r"\bgraved\b", r"\bmarinated\b", r"\bcured\b"),
                product_terms=_CURED_TERMS,
            ),
        ),
    ),
    # T39: a named cheese variety is a hard type; a mix with another variety or a processed cheese is not it.
    TypeQualifierGroup(
        group_id="cheese_variety",
        concepts=frozenset(),
        variants=tuple(
            TypeVariant(
                variety_id, label, patterns, product_terms=terms,
                forbid_terms=(
                    *_PROCESSED_CHEESE_TERMS,
                    *(t for other_id, _, _, other_terms in _CHEESE_VARIETIES if other_id != variety_id
                      for t in other_terms),
                ),
                forbid_label="processed cheese",
            )
            for variety_id, label, patterns, terms in _CHEESE_VARIETIES
        ),
    ),
    # T52: salted or unsalted, as the buyer stated it (butter, nuts, crackers …).
    TypeQualifierGroup(
        group_id="salt",
        concepts=frozenset(),
        variants=(
            TypeVariant(
                "unsalted", "unsalted",
                (r"\bunsalted\b", r"\bsuolaton\w*", r"\bsuolatont\w*", r"\bosaltat\w*", r"\bsaltfri\w*",
                 r"\bwithout\s+salt\b"),
                product_terms=_UNSALTED_TERMS,
            ),
            TypeVariant(
                "salted", "salted",
                (r"\bsalted\b", r"\bsuolat(?:tu|tua|tun|tuja)\b", r"\bnormaalisuolai\w*", r"\bsaltat\w*"),
                product_terms=_SALTED_TERMS,
                forbid_terms=_UNSALTED_TERMS,
            ),
        ),
    ),
    # T39: butter is not margarine, a fat spread or a butter-oil blend.
    TypeQualifierGroup(
        group_id="butter_vs_spread",
        concepts=frozenset({"butter"}),
        variants=(
            TypeVariant(
                "butter", "butter",
                (r"\bbutter\b", r"\bvoi(ta|n|lla|ssa|hin)?\b", r"\w*voita\b", r"\bsmör\w*"),
                product_terms=("butter", "voi", "smör"),
                forbid_terms=_SPREAD_TERMS,
            ),
            TypeVariant(
                "spread", "margarine or fat spread",
                (r"\bmargarin\w*", r"\bmargariin\w*", r"\blevite\w*", r"\brasvaseos\w*", r"\bspreads?\b"),
                product_terms=_SPREAD_TERMS,
            ),
        ),
    ),
)

# Requested concepts that name a dairy product: a plant-based alternative is a different product type.
DAIRY_CONCEPTS: frozenset[str] = frozenset({"milk", "cream", "sour_cream", "yoghurt", "quark", "butter", "cheese"})

ALTERNATIVE_PRODUCT_TERMS: tuple[str, ...] = (
    "alternative", "vaihtoehto", "plant-based", "plant based", "kasvipohj", "kasvirasva", "vegetable fat",
    "växtbaser", "växtfett", "vegetabilisk", "oat ", "oat-", "kaura", "havre", "soy", "soija", "soja",
    "coconut", "kookos", "kokos", "almond", "manteli", "mandel",
)
_ASKED_FOR_ALTERNATIVE = re.compile(
    r"\b(alternative|vaihtoehto\w*|plant[- ]based|kasvi\w*|vegan\w*|vegaani\w*|oat\w*|kaura\w*|havre\w*|"
    r"soy\w*|soija\w*|soja\w*|coconut|kookos\w*|kokos\w*|almond|manteli\w*|mandel\w*|dairy[- ]free|"
    r"milk[- ]free|maidoton\w*|mjölkfri\w*|växt\w*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TypeMatch:
    """Whether a product is exactly the type the buyer asked for; `differences` explain why not."""

    exact: bool
    differences: tuple[str, ...] = ()


def requested_variants(query: str, concept_id: str | None) -> tuple[tuple[TypeQualifierGroup, TypeVariant], ...]:
    """The use-changing qualifiers the buyer stated, at most one per group (the first one stated)."""
    q = query.lower()
    found: list[tuple[TypeQualifierGroup, TypeVariant]] = []
    for group in TYPE_QUALIFIER_GROUPS:
        if group.concepts and concept_id not in group.concepts:
            continue
        hits = [
            (min(m.start() for p in v.query_patterns for m in re.finditer(p, q)), v)
            for v in group.variants
            if any(re.search(p, q) for p in v.query_patterns)
        ]
        if hits:
            found.append((group, min(hits, key=lambda hit: hit[0])[1]))
    return tuple(found)


def asked_for_alternative(query: str, requirement_codes: tuple[str, ...] = ()) -> bool:
    """A plant-based alternative is acceptable when the buyer asked for one, or for vegan / milk-free."""
    return bool(_ASKED_FOR_ALTERNATIVE.search(query)) or "VEGAN" in requirement_codes or "AM" in requirement_codes


def product_type_match(
    query: str,
    product_names: tuple[str, ...],
    *,
    concept_id: str | None,
    requirement_codes: tuple[str, ...] = (),
) -> TypeMatch:
    """Check a product against the product type in the buyer's words (names in every language count)."""
    text = " " + " ".join(n for n in product_names if n).lower() + " "
    differences: list[str] = []
    for group, variant in requested_variants(query, concept_id):
        proves = not variant.product_terms or any(t in text for t in variant.product_terms)
        disproves = any(t in text for t in variant.forbid_terms)
        if proves and not disproves:
            continue
        other = next(
            (v for v in group.variants if v is not variant and v.product_terms and any(t in text for t in v.product_terms)),
            None,
        )
        if other is not None and proves and variant.product_terms:
            differences.append(f"mixed with {other.label}, not only {variant.label}")
        elif other is not None:
            differences.append(f"{other.label}, not {variant.label}")
        elif disproves and variant.forbid_label:
            differences.append(f"{variant.forbid_label}, not {variant.label}")
        else:
            differences.append(f"not {variant.label}")
    if (
        concept_id in DAIRY_CONCEPTS
        and not asked_for_alternative(query, requirement_codes)
        and any(t in text for t in ALTERNATIVE_PRODUCT_TERMS)
    ):
        differences.append("plant-based or blended alternative, not dairy")
    return TypeMatch(exact=not differences, differences=tuple(differences))


def strip_type_qualifiers(query: str) -> str:
    """The query without the use-changing qualifier words (fresh, frozen, whipping …): what remains names
    the product itself (e.g. "salmon fillet")."""
    q = query.lower()
    for group in TYPE_QUALIFIER_GROUPS:
        for variant in group.variants:
            for pattern in variant.query_patterns:
                q = re.sub(pattern, " ", q)
    return " ".join(q.split())
