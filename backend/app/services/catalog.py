from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product
from app.schemas import ProductOut


def product_details(db: Session, code: str) -> ProductOut:
    product = db.get(Product, code)
    if not product:
        raise HTTPException(400, f"Unknown product code: {code}")
    return ProductOut.model_validate(product.data)


def search(db, provider, query="", category=None, dietary=None, exclude_allergen=None, limit=20):
    stmt = select(Product).order_by(Product.code)
    if category:
        stmt = stmt.where(Product.category == category)
    products = [ProductOut.model_validate(p.data) for p in db.scalars(stmt)]
    products = provider.search_products(products, query)
    if dietary:
        products = [p for p in products if p.dietary.get(dietary) is True]
    if exclude_allergen:
        # Missing information is not evidence of safety: exclude unknowns in filtered search.
        products = [
            p
            for p in products
            if p.allergens is not None and exclude_allergen.strip().casefold() not in p.allergens
        ]
    return products[:limit]
