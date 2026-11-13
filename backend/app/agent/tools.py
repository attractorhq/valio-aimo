import json

from fastapi import HTTPException
from pydantic import Field, ValidationError

from app.schemas import OfferRequest, ProposalRequest, StrictModel
from app.services import catalog, orders


class SearchInput(StrictModel):
    query: str = Field(default="", max_length=200)
    category: str | None = None
    dietary: str | None = None
    exclude_allergen: str | None = None
    limit: int = Field(default=10, ge=1, le=20)


class ProductInput(StrictModel):
    product_code: str = Field(min_length=1, max_length=100)


class StatusInput(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=100)


TOOL_MODELS = {
    "search_products": SearchInput,
    "get_product_details": ProductInput,
    "check_offer": OfferRequest,
    "create_order_proposal": ProposalRequest,
    "get_proposal_status": StatusInput,
}
DESCRIPTIONS = {
    "search_products": "Search local catalog. Dietary/allergen filters only return confirmed matches. No price calls.",
    "get_product_details": "Retrieve exact pack unit, dietary facts and unknowns for a product code.",
    "check_offer": "Check fixed price and delivery-date stock for shortlisted items; does not reserve or order.",
    "create_order_proposal": "Create a validated proposal with ALL buyer restrictions; never commits a purchase.",
    "get_proposal_status": "Read an authorized proposal and its human approval/commit status.",
}


def definitions():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": model.model_json_schema(),
            },
        }
        for name, model in TOOL_MODELS.items()
    ]


def execute_tool(name, arguments, db, provider, actor):
    if name not in TOOL_MODELS:
        return {"error": "TOOL_NOT_ALLOWED"}
    try:
        args = TOOL_MODELS[name].model_validate_json(arguments)
        if name == "search_products":
            result = catalog.search(db, provider, **args.model_dump())
            return {"products": [p.model_dump(mode="json") for p in result]}
        if name == "get_product_details":
            return catalog.product_details(db, args.product_code).model_dump(mode="json")
        if name == "check_offer":
            offer = orders.check_offer(db, provider, args)
            # Deterministic candidate ranking; no model arithmetic. Caller must first filter constraints.
            ranked = sorted(
                offer.items,
                key=lambda i: (
                    not i.available,
                    i.line_total,
                    -(catalog.product_details(db, i.product_code).reliability or 0),
                    i.product_code,
                ),
            )
            return {**offer.model_dump(mode="json"), "ranked_product_codes": [i.product_code for i in ranked]}
        if name == "create_order_proposal":
            return orders.create_proposal(db, provider, actor, args).model_dump(mode="json")
        return orders.serialize(orders.get_proposal(db, actor, args.proposal_id)).model_dump(mode="json")
    except (ValidationError, ValueError, json.JSONDecodeError):
        return {
            "error": "INVALID_TOOL_INPUT",
            "message": "Arguments do not match the documented tool schema.",
        }
    except HTTPException as exc:
        return {"error": f"HTTP_{exc.status_code}", "message": exc.detail}
