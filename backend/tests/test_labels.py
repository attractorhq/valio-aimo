"""T75: Certifications and origin as hard requirements (MSC, ASC, organic/luomu, Fairtrade, Finnish origin)."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.agent.explain import explain_proposal, no_order_item_reason
from app.agent.parse import parse_request
from app.catalog.build import DEFAULT_ARTIFACT_PATH, DEFAULT_RAW_DIR, load_catalog
from app.catalog.normalise import normalise_product
from app.catalog.search import evaluate_product_static_requirements, search_products
from app.contracts import ReasonCode, Requirement
from app.gateway.offers import requirement_facts
from app.policy import M1, M2, check_requirements
from app.policy.requirements import (
    FINNISH_ORIGIN_LABELS,
    ORGANIC_LABELS,
    claim_requirement_fact,
)
from tests.synthetic.catalog_records import synthetic_product_record

HKI = ZoneInfo("Europe/Helsinki")
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=HKI)

MSC_REQ = Requirement(kind="CLAIM", code="MARINE_STEWARDSHIP_COUNCIL_LABEL")
ASC_REQ = Requirement(kind="CLAIM", code="AQUACULTURE_STEWARDSHIP_COUNCIL")
ORGANIC_REQ = Requirement(kind="CLAIM", code="EU_ORGANIC_FARMING")
FAIRTRADE_REQ = Requirement(kind="CLAIM", code="FAIR_TRADE_MARK")
FINNISH_REQ = Requirement(kind="CLAIM", code="FINNISH_ORIGIN")


def _make_product(
    product_id: str,
    *,
    en_name: str,
    fi_name: str | None = None,
    category: str = "10400",
    labels: list[str] | None = None,
    country_of_origin: str | None = None,
    sales_unit: str = "KG",
):
    names = {"en": en_name}
    if fi_name:
        names["fi"] = fi_name
    rec = synthetic_product_record(
        product_id=product_id,
        category=category,
        sales_unit=sales_unit,
        base_unit=sales_unit,
        names=names,
        allergens=[("AM", "CONTAINS")],
        claims=["FREE_FROM_LACTOSE"],
        labels=labels or [],
        country_of_origin=country_of_origin,
    )
    return normalise_product(rec)


# --------------------------------------------------------------------------- 1. Parser (EN & FI)


@pytest.mark.parametrize(
    ("text", "expected_what", "expected_req"),
    [
        ("20 kg salmon fillet, MSC certified, by Wednesday", "salmon fillet", MSC_REQ),
        ("Tarvitsemme 20 kg MSC-merkittyä lohifileetä keskiviikoksi", "salmon fillet", MSC_REQ),
        ("20 kg MSC-sertifioitua lohta huomiseksi", "salmon", MSC_REQ),
        ("20 kg salmon fillet, ASC certified, tomorrow", "salmon fillet", ASC_REQ),
        ("20 kg ASC-merkittyä lohifileetä huomiseksi", "salmon fillet", ASC_REQ),
        ("luomu maitoa 50 litraa", "milk", ORGANIC_REQ),
        ("50 litraa luomumaitoa huomiseksi", "milk", ORGANIC_REQ),
        ("50 l organic milk by Monday", "milk", ORGANIC_REQ),
        ("10 kg Fairtrade coffee tomorrow", "coffee", FAIRTRADE_REQ),
        ("Tarvitsemme 10 kg kahvia, Reilun kaupan, huomiseksi", "coffee", FAIRTRADE_REQ),
        ("100 l Finnish milk by Monday", "milk", FINNISH_REQ),
        ("100 l kotimaista maitoa maanantaiksi", "milk", FINNISH_REQ),
        ("50 kg suomalaista voita huomiseksi", "butter", FINNISH_REQ),
    ],
)
def test_parse_certification_and_origin_as_hard_requirements(text, expected_what, expected_req):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    assert result.request is not None
    (item,) = result.request.items
    assert item.what == expected_what
    assert expected_req in item.requirements


# --------------------------------------------------------------------------- 2. Policy & TrustLabel facts


def test_msc_and_asc_policy_exclusion_detail_and_verified_fact():
    plain_salmon = _make_product("SYN-SAL-PLAIN", en_name="Synthetic salmon fillet 1 kg", category="174")
    msc_salmon = _make_product(
        "SYN-SAL-MSC",
        en_name="Synthetic salmon fillet 1 kg",
        category="174",
        labels=["MARINE_STEWARDSHIP_COUNCIL_LABEL"],
    )
    asc_salmon = _make_product(
        "SYN-SAL-ASC",
        en_name="Synthetic salmon fillet 1 kg",
        category="174",
        labels=["AQUACULTURE_STEWARDSHIP_COUNCIL"],
    )

    # Missing MSC -> EXCLUDE with CLAIM_MISSING and clear detail "No MSC label in product data"
    findings = check_requirements(plain_salmon, [MSC_REQ], M1)
    assert len(findings) == 1
    assert findings[0].code == ReasonCode.CLAIM_MISSING
    assert findings[0].effect == "EXCLUDE"
    assert findings[0].detail == "No MSC label in product data"

    # ASC does not satisfy MSC
    findings_asc = check_requirements(asc_salmon, [MSC_REQ], M1)
    assert len(findings_asc) == 1
    assert findings_asc[0].detail == "No MSC label in product data"

    # Present MSC -> passes and produces VERIFIED fact with source (product data, labels)
    assert check_requirements(msc_salmon, [MSC_REQ], M1) == ()
    fact = claim_requirement_fact(msc_salmon, "MARINE_STEWARDSHIP_COUNCIL_LABEL")
    assert fact.level == "VERIFIED"
    assert fact.value is True
    assert fact.source.system == "valio-aimo.product-data"
    assert fact.source.field == "labels"
    assert "MSC" in fact.display

    # Missing ASC -> "No ASC label in product data"
    findings_no_asc = check_requirements(plain_salmon, [ASC_REQ], M1)
    assert len(findings_no_asc) == 1
    assert findings_no_asc[0].detail == "No ASC label in product data"
    assert check_requirements(asc_salmon, [ASC_REQ], M1) == ()


@pytest.mark.parametrize("org_label", ["EU_ORGANIC_FARMING", "KRAV_MARK", "SOIL_ASSOCIATION_ORGANIC_SYMBOL"])
def test_organic_requirement_accepts_eu_organic_krav_and_soil_association(org_label):
    org_milk = _make_product("SYN-ORG-MILK", en_name="Synthetic milk 1 l", labels=[org_label], sales_unit="LTR")
    plain_milk = _make_product("SYN-PLAIN-MILK", en_name="Synthetic milk 1 l", labels=[], sales_unit="LTR")

    assert check_requirements(org_milk, [ORGANIC_REQ], M1) == ()
    fact = claim_requirement_fact(org_milk, "EU_ORGANIC_FARMING")
    assert fact.level == "VERIFIED"
    assert fact.value is True
    assert fact.source.system == "valio-aimo.product-data"
    assert fact.source.field == "labels"
    assert org_label in fact.display

    findings = check_requirements(plain_milk, [ORGANIC_REQ], M1)
    assert len(findings) == 1
    assert findings[0].code == ReasonCode.CLAIM_MISSING
    assert findings[0].detail == "No organic label in product data"


def test_fairtrade_requirement_and_missing_detail():
    ft_coffee = _make_product("SYN-FT-COFFEE", en_name="Synthetic coffee 1 kg", category="223", labels=["FAIR_TRADE_MARK"])
    plain_coffee = _make_product("SYN-PLAIN-COFFEE", en_name="Synthetic coffee 1 kg", category="223", labels=[])

    assert check_requirements(ft_coffee, [FAIRTRADE_REQ], M1) == ()
    fact = claim_requirement_fact(ft_coffee, "FAIR_TRADE_MARK")
    assert fact.level == "VERIFIED"
    assert fact.source.system == "valio-aimo.product-data"
    assert fact.source.field == "labels"
    assert "Fairtrade" in fact.display

    findings = check_requirements(plain_coffee, [FAIRTRADE_REQ], M1)
    assert len(findings) == 1
    assert findings[0].code == ReasonCode.CLAIM_MISSING
    assert findings[0].detail == "No Fairtrade label in product data"


@pytest.mark.parametrize(
    "fi_label",
    ["GOODS_FROM_FINLAND_BLUE_SWAN", "FINNISH_HEART_SYMBOL", "MADE_IN_FINLAND_FLAG_WITH_KEY"],
)
def test_finnish_origin_satisfied_by_finnish_label_or_country_of_origin_and_says_which(fi_label):
    by_label = _make_product("SYN-FI-LBL", en_name="Synthetic milk 1 l", labels=[fi_label], country_of_origin=None)
    by_country = _make_product("SYN-FI-COO", en_name="Synthetic milk 1 l", labels=[], country_of_origin="FI")
    by_both = _make_product("SYN-FI-BOTH", en_name="Synthetic milk 1 l", labels=[fi_label], country_of_origin="fi")
    foreign = _make_product("SYN-SE", en_name="Synthetic milk 1 l", labels=[], country_of_origin="se")

    # 1. Satisfied by Finnish-origin label
    assert check_requirements(by_label, [FINNISH_REQ], M1) == ()
    f_lbl = requirement_facts(by_label, [FINNISH_REQ], M1)[0]
    assert f_lbl.level == "VERIFIED"
    assert f_lbl.value is True
    assert f_lbl.source.system == "valio-aimo.product-data"
    assert f_lbl.source.field == "labels"
    assert fi_label in f_lbl.display

    # 2. Satisfied by countryOfOrigin = FI
    assert check_requirements(by_country, [FINNISH_REQ], M1) == ()
    f_coo = requirement_facts(by_country, [FINNISH_REQ], M1)[0]
    assert f_coo.level == "VERIFIED"
    assert f_coo.value is True
    assert f_coo.source.system == "valio-aimo.product-data"
    assert f_coo.source.field == "countryOfOrigin"
    assert "countryOfOrigin = FI" in f_coo.display

    # 3. Satisfied by both: names both in display
    assert check_requirements(by_both, [FINNISH_REQ], M1) == ()
    f_both = requirement_facts(by_both, [FINNISH_REQ], M1)[0]
    assert f_both.level == "VERIFIED"
    assert fi_label in f_both.display and "countryOfOrigin = FI" in f_both.display

    # 4. Neither present -> EXCLUDE with CLAIM_MISSING
    findings = check_requirements(foreign, [FINNISH_REQ], M1)
    assert len(findings) == 1
    assert findings[0].code == ReasonCode.CLAIM_MISSING
    assert findings[0].detail == "No Finnish-origin label or countryOfOrigin = FI in product data"


# --------------------------------------------------------------------------- 3. Search & Acceptance checks


def test_synthetic_search_msc_salmon_fillet_filters_and_explains_exclusions():
    msc_salmon = _make_product(
        "SYN-SAL-MSC",
        en_name="Synthetic MSC salmon fillet 1 kg",
        fi_name="Synteettinen MSC lohifilee 1 kg",
        category="174",
        labels=["MARINE_STEWARDSHIP_COUNCIL_LABEL"],
    )
    asc_salmon = _make_product(
        "SYN-SAL-ASC",
        en_name="Synthetic ASC salmon fillet 1 kg",
        fi_name="Synteettinen ASC lohifilee 1 kg",
        category="174",
        labels=["AQUACULTURE_STEWARDSHIP_COUNCIL"],
    )
    plain_salmon = _make_product(
        "SYN-SAL-PLAIN",
        en_name="Synthetic salmon fillet 1 kg",
        fi_name="Synteettinen lohifilee 1 kg",
        category="174",
        labels=[],
    )

    parsed = parse_request("20 kg salmon fillet, MSC certified, by Wednesday", NOW).request
    assert parsed is not None
    res = search_products(parsed.items[0], mandate=M1, products=[msc_salmon, asc_salmon, plain_salmon])
    assert [c.productId for c in res.candidates] == ["SYN-SAL-MSC"]
    msc_fact = next(f for f in res.candidates[0].facts if f.fact == "claim.MARINE_STEWARDSHIP_COUNCIL_LABEL")
    assert msc_fact.level == "VERIFIED"
    assert msc_fact.source.system == "valio-aimo.product-data"
    assert msc_fact.source.field == "labels"

    ex_by_id = {e.productId: e for e in res.excluded}
    assert set(ex_by_id) == {"SYN-SAL-ASC", "SYN-SAL-PLAIN"}
    for ex in ex_by_id.values():
        assert ReasonCode.CLAIM_MISSING in ex.reasons
        assert any(f.display == "No MSC label in product data" for f in ex.facts)


def test_no_order_names_missing_msc_label_when_no_candidate_has_msc():
    asc_salmon = _make_product(
        "SYN-SAL-ASC",
        en_name="Synthetic ASC salmon fillet 1 kg",
        fi_name="Synteettinen ASC lohifilee 1 kg",
        category="174",
        labels=["AQUACULTURE_STEWARDSHIP_COUNCIL"],
    )
    parsed = parse_request("20 kg salmon fillet, MSC certified, by Wednesday", NOW).request
    assert parsed is not None
    item = parsed.items[0]
    res = search_products(item, mandate=M2, products=[asc_salmon])
    assert res.candidates == []
    assert len(res.excluded) == 1
    assert ReasonCode.CLAIM_MISSING in res.excluded[0].reasons
    assert res.excluded[0].facts[0].display == "No MSC label in product data"

    why = no_order_item_reason(
        has_candidates=False,
        deadline_label="Wed 18 Nov",
        delivery_label=None,
        delivered=False,
        excluded=res.excluded,
        requirements=item.requirements,
    )
    assert "MSC" in why
    assert "No MSC label in product data" in why

    exp = explain_proposal(reasons=[ReasonCode.NO_CANDIDATE], excluded=res.excluded, mandate=M2)
    assert "No MSC label in product data" in exp.text


def test_real_catalog_msc_salmon_fillet_and_luomu_maitoa():
    if not DEFAULT_ARTIFACT_PATH.exists() and not (DEFAULT_RAW_DIR / "products.json").exists():
        pytest.skip("Real catalogue data not present on this machine")

    catalog = load_catalog()

    # Acceptance 1: "20 kg salmon fillet, MSC certified, by Wednesday" -> only MSC-labelled salmon,
    # or a no-order that names the missing label.
    msc_req = parse_request("20 kg salmon fillet, MSC certified, by Wednesday", NOW).request
    assert msc_req is not None
    msc_res = search_products(msc_req.items[0], mandate=M1, products=catalog)
    if msc_res.candidates:
        assert all("MARINE_STEWARDSHIP_COUNCIL_LABEL" in c.labels for c in msc_res.candidates)
    else:
        assert msc_res.excluded
        assert all(ReasonCode.CLAIM_MISSING in e.reasons for e in msc_res.excluded)
        assert all(any(f.display == "No MSC label in product data" for f in e.facts) for e in msc_res.excluded)
        why = no_order_item_reason(
            has_candidates=False,
            deadline_label="Wed 18 Nov",
            delivery_label=None,
            delivered=False,
            excluded=msc_res.excluded,
            requirements=msc_req.items[0].requirements,
        )
        assert "MSC" in why and "No MSC label in product data" in why

    # Acceptance 2: "luomu maitoa 50 litraa" -> only organic products.
    luomu_req = parse_request("luomu maitoa 50 litraa", NOW).request
    assert luomu_req is not None
    assert luomu_req.items[0].quantity == Decimal("50")
    assert luomu_req.items[0].unit == "l"
    assert ORGANIC_REQ in luomu_req.items[0].requirements

    luomu_res = search_products(luomu_req.items[0], mandate=M1, products=catalog)
    assert luomu_res.candidates, "Expected organic milk products in the catalogue"
    for cand in luomu_res.candidates:
        assert set(cand.labels).intersection(ORGANIC_LABELS)
        fact = next(f for f in cand.facts if f.fact == "claim.EU_ORGANIC_FARMING")
        assert fact.level == "VERIFIED"
        assert fact.source.system == "valio-aimo.product-data"
        assert fact.source.field == "labels"

    # Direct inline search query also returns only organic products
    direct_luomu = search_products("luomu maitoa 50 litraa", mandate=M1, products=catalog)
    assert [c.productId for c in direct_luomu.candidates] == [c.productId for c in luomu_res.candidates]

    # Finnish origin search on real catalogue: every candidate has a Finnish label or countryOfOrigin = FI
    fi_req = parse_request("100 l kotimaista maitoa huomiseksi", NOW).request
    assert fi_req is not None
    fi_res = search_products(fi_req.items[0], mandate=M1, products=catalog)
    assert fi_res.candidates
    for cand in fi_res.candidates:
        has_lbl = bool(set(cand.labels).intersection(FINNISH_ORIGIN_LABELS))
        has_coo = (cand.countryOfOrigin or "").lower() == "fi"
        assert has_lbl or has_coo
        _, facts = evaluate_product_static_requirements(cand, fi_req.items[0].requirements, M1)
        assert facts and facts[0].level == "VERIFIED"
