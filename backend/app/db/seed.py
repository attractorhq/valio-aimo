from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Policy, Product
from app.schemas import ProductOut

# Entirely invented challenge fixtures; not Valio product claims or real prices.
FIXTURES = [
    (
        "DEMO-MLK-01",
        "Lactose-free whole milk",
        "dairy",
        "L",
        "1",
        {"lactose_free": True},
        ["milk"],
        0.98,
        1.3,
    ),
    (
        "DEMO-MLK-02",
        "Lactose-free semi-skimmed milk",
        "dairy",
        "L",
        "1",
        {"lactose_free": True},
        ["milk"],
        0.96,
        1.3,
    ),
    (
        "DEMO-OAT-01",
        "Barista oat drink",
        "plant_based",
        "L",
        "1",
        {"lactose_free": True, "vegan": True},
        ["oats", "gluten"],
        0.97,
        0.5,
    ),
    (
        "DEMO-MLK-03",
        "Organic whole milk",
        "dairy",
        "L",
        "1",
        {"lactose_free": False, "organic": True},
        ["milk"],
        0.99,
        1.3,
    ),
    (
        "DEMO-MLK-04",
        "Lactose-free milk · out of stock",
        "dairy",
        "L",
        "1",
        {"lactose_free": True},
        ["milk"],
        0.88,
        1.3,
    ),
    ("DEMO-VEG-01", "Seasonal kitchen vegetables", "produce", "KG", "5", {"vegan": True}, None, None, None),
    (
        "DEMO-RIC-01",
        "Long-grain rice · 5 kg pack",
        "pantry",
        "KG",
        "5",
        {"vegan": True, "gluten_free": True},
        [],
        0.99,
        2.7,
    ),
    ("DEMO-SUP-01", "Kitchen soup base · unverified allergens", "pantry", "L", "1", {}, None, None, None),
]


def seed(db: Session, include_products: bool = True):
    if include_products:
        for code, name, category, unit, pack, dietary, allergens, reliability, carbon in FIXTURES:
            if not db.get(Product, code):
                data = ProductOut(
                    code=code,
                    name=name,
                    category=category,
                    unit=unit,
                    pack_quantity=pack,
                    dietary=dietary,
                    allergens=allergens,
                    reliability=reliability,
                    carbon_kg_co2_per_kg=carbon,
                    carbon_scope="category_average" if carbon is not None else None,
                    source="Synthetic demo fixture",
                ).model_dump(mode="json")
                db.add(Product(code=code, name=name, category=category, data=data))
    categories = (
        ["dairy", "plant_based", "produce", "pantry"]
        if include_products
        else sorted(set(db.scalars(select(Product.category))))
    )
    equivalents = (
        {"DEMO-MLK-01": ["DEMO-MLK-02"]}
        if include_products
        else {
            p.code: p.data["substitution_codes"]
            for p in db.scalars(select(Product))
            if p.data.get("substitution_codes")
        }
    )
    for buyer, name, limit, equivalent in [
        ("restaurant-a", "Restaurant A", "200.00", False),
        ("hotel-b", "Hotel B", "500.00", True),
    ]:
        if not db.get(Policy, buyer):
            db.add(
                Policy(
                    buyer_id=buyer,
                    name=name,
                    version=1,
                    data={
                        "max_auto_total": limit,
                        "allowed_categories": categories,
                        "allow_equivalent_substitutions": equivalent,
                        "equivalent_substitutions": equivalents,
                        "excluded_allergens": [],
                        "unusual_quantity": "1000",
                        "confirmed_availability_required": True,
                    },
                )
            )
    db.commit()
