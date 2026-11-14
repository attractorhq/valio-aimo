"""T74: search resolves the most specific product type on synthetic records: cream cheese is never a cream,
"kermajuusto" finds tilsit before any other cheese, milk powder is never liquid milk, and a fat spread is
the fat family, never tea."""

from app.catalog.normalise import normalise_product
from app.catalog.search import search_products
from tests.synthetic.catalog_records import synthetic_product_record


def _product(product_id: str, category: str, en: str, fi: str, *, lactose_free: bool = True):
    return normalise_product(
        synthetic_product_record(
            product_id=product_id,
            category=category,
            names={"en": en, "fi": fi},
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"] if lactose_free else [],
            unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "weight": {"unit": "KGM", "value": 1}}],
        )
    )


POOL = [
    _product("SYN-COOK", "10401", "Synthetic cooking cream 1 l", "Synteettinen ruokakerma 1 l"),
    _product("SYN-WHIP", "10401", "Synthetic whipping cream 1 l", "Synteettinen vispikerma 1 l"),
    _product("SYN-CC", "10635", "Synthetic natural cream cheese 1,5 kg", "Synteettinen maustamaton tuorejuusto 1,5 kg"),
    _product("SYN-PROC", "10633", "Synthetic processed cheese 2,5 kg", "Synteettinen sulatejuusto 2,5 kg"),
    _product("SYN-TILSIT", "10602", "Synthetic tilsit cheese slices 700 g", "Synteettinen kermajuustoviipale 700 g"),
    _product("SYN-BLUE", "10632", "Synthetic blue cheese 100 g", "Synteettinen sinihomejuusto 100 g"),
    _product("SYN-MILK", "10400", "Synthetic semi skimmed milk 1 l", "Synteettinen kevytmaito 1 l"),
    _product("SYN-POWDER", "10405", "Synthetic skimmed milk powder 25 kg", "Synteettinen rasvaton maitojauhe 25 kg"),
    _product("SYN-BUTTER", "10500", "Synthetic butter 500 g", "Synteettinen voi 500 g"),
    _product("SYN-SPREAD", "10515", "Synthetic vegetable fat spread 70 % 400 g",
             "Synteettinen kasvirasvalevite 70 % 400 g", lactose_free=False),
    _product("SYN-TEA", "22531", "Synthetic green loose tea 100 g", "Synteettinen vihreä irtotee 100 g"),
]


def _search(query: str):
    return search_products(query, products=POOL)


def test_cream_cheese_is_never_a_cream():
    for query in ("cream cheese", "lactose-free cream cheese", "tuorejuusto"):
        res = _search(query)
        assert res.resolvedProductType == "cream_cheese", query
        assert [c.productId for c in res.candidates] == ["SYN-CC"], query


def test_kermajuusto_finds_tilsit_first_and_never_a_cream():
    res = _search("kermajuusto")
    assert res.resolvedProductType == "cheese"
    ids = [c.productId for c in res.candidates]
    assert ids[0] == "SYN-TILSIT"
    assert not {"SYN-COOK", "SYN-WHIP"} & set(ids)
    assert _search("tilsit cheese").candidates[0].productId == "SYN-TILSIT"


def test_processed_cheese_is_its_own_type():
    res = _search("processed cheese")
    assert res.resolvedProductType == "processed_cheese"
    assert [c.productId for c in res.candidates] == ["SYN-PROC"]


def test_milk_powder_is_never_liquid_milk():
    for query in ("milk powder", "maitojauhe"):
        res = _search(query)
        assert res.resolvedProductType == "milk_powder", query
        assert [c.productId for c in res.candidates] == ["SYN-POWDER"], query
    assert [c.productId for c in _search("milk").candidates] == ["SYN-MILK"]


def test_a_fat_spread_is_the_fat_family_never_tea():
    for query in ("levite", "fat spread", "kasvirasvalevite"):
        res = _search(query)
        assert res.resolvedProductType == "butter", query
        assert res.candidates[0].productId == "SYN-SPREAD", query
        assert "SYN-TEA" not in [c.productId for c in res.candidates], query
    # Butter still means butter, not the spread.
    assert _search("butter").candidates[0].productId == "SYN-BUTTER"
