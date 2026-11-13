from app.schemas import OfferOut, PolicyOut, ProductOut, ProposalRequest, Reason


def evaluate(policy: PolicyOut, request: ProposalRequest, offer: OfferOut, products: dict[str, ProductOut]):
    reject: list[Reason] = []
    escalate: list[Reason] = []

    def add(target, code, message):
        if not any(r.code == code and r.message == message for r in target):
            target.append(Reason(code=code, message=message))

    restrictions = {x.strip().lower() for x in policy.excluded_allergens + request.excluded_allergens}
    for item, checked in zip(request.items, offer.items, strict=True):
        p = products[item.product_code]
        if p.category not in policy.allowed_categories:
            add(reject, "CATEGORY_FORBIDDEN", f"{p.name}: category is outside buyer policy.")
        if not checked.available:
            add(reject, "UNAVAILABLE", f"{p.name}: insufficient stock or delivery date cannot be met.")
        if restrictions:
            if p.allergens is None:
                add(
                    escalate,
                    "ALLERGEN_DATA_UNKNOWN",
                    f"{p.name}: allergen information is unknown; human review required.",
                )
            elif restrictions.intersection(a.lower() for a in p.allergens):
                add(
                    reject,
                    "ALLERGEN_VIOLATION",
                    f"{p.name}: conflicts with an explicit allergen restriction.",
                )
        for dietary in request.required_dietary:
            known = p.dietary.get(dietary.lower())
            if known is False:
                add(reject, "DIETARY_VIOLATION", f"{p.name}: does not meet {dietary}.")
            elif known is None:
                add(escalate, "DIETARY_DATA_UNKNOWN", f"{p.name}: {dietary} is not verified.")
        if item.substitute_for:
            equivalents = policy.equivalent_substitutions.get(item.substitute_for, [])
            if not policy.allow_equivalent_substitutions or p.code not in equivalents:
                add(
                    escalate,
                    "SUBSTITUTION_REQUIRES_APPROVAL",
                    f"{p.name}: substitution is not pre-authorized.",
                )
        if item.quantity > policy.unusual_quantity:
            add(
                escalate,
                "UNUSUAL_QUANTITY",
                f"{p.name}: quantity exceeds the illustrative normal-order threshold.",
            )
    if offer.total > policy.max_auto_total:
        add(escalate, "POLICY_LIMIT_EXCEEDED", "Order exceeds the buyer's automatic approval limit.")
    if request.budget is not None and offer.total > request.budget:
        add(
            escalate,
            "REQUEST_BUDGET_EXCEEDED",
            "Order exceeds the stated budget; explicit human authorization required.",
        )
    if reject:
        return "REJECTED", reject + escalate
    if escalate:
        return "NEEDS_APPROVAL", escalate
    return "AUTO_APPROVED", [
        Reason(code="WITHIN_POLICY", message="Available at checked terms and within buyer authority.")
    ]
