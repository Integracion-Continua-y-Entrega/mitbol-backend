"""HU-019: reglas explícitas, importes en centavos e historial sin canjear cupones."""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, localcontext
import json

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import object_session

from app.models import CommercialDiscountAssignment, Discount, OrderDiscountSnapshot
from app.schemas.commercial_discounts import (
    AppliedCommercialDiscount, CommercialDiscountConditions, DiscountAllocation, PendingCommercialDiscount,
)
from app.schemas.order_summary import OrderSummaryItem, OrderSummaryResponse


CENT = Decimal("0.01")
ZERO = Decimal("0")
PERCENT_TYPES = {"FIXED_PERCENT", "VARIABLE_PERCENT"}
AMOUNT_TYPES = {"FIXED_AMOUNT", "VARIABLE_AMOUNT"}


def schema_available(db):
    # Permite desplegar el código antes de 0013 conservando los cálculos previos.
    return db.scalar(text("""SELECT to_regclass('public."CommercialDiscountAssignments"') IS NOT NULL
        AND to_regclass('public."OrderDiscountSnapshots"') IS NOT NULL"""))


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _allocate(amount, balances):
    """Prorrateo en centavos: restos mayores, desempate por posición del carrito."""
    total = sum(balances, ZERO)
    if not total or not amount:
        return [ZERO for _ in balances]
    shares = [amount * balance / total for balance in balances]
    parts = [min(balance, share.quantize(CENT, rounding=ROUND_DOWN)) for balance, share in zip(balances, shares)]
    remaining = int((amount - sum(parts, ZERO)) / CENT)
    for index in sorted(range(len(parts)), key=lambda index: (-(shares[index] - parts[index]), index)):
        if remaining and parts[index] + CENT <= balances[index]:
            parts[index] += CENT
            remaining -= 1
    assert remaining == 0
    return parts


def calculate(summary, records):
    """Función común pura. records contiene descuentos y asociaciones leídas del backend."""
    pending, candidates = [], []

    def defer(discount, reason):
        pending.append(PendingCommercialDiscount(discount_id=discount.discount_id, name=discount.name, reason=reason))

    for discount, assignments in records:
        if not discount.is_active:
            continue
        mapped = {row.product_id: row.conditions for row in assignments}
        indexes = [index for index, row in enumerate(summary.items) if row.selection.product_id in mapped]
        if not indexes:
            if not assignments:
                defer(discount, "eligibility_pending")
            continue
        try:
            rules = {index: CommercialDiscountConditions.model_validate(mapped[summary.items[index].selection.product_id])
                     for index in indexes}
        except ValidationError:
            defer(discount, "conditions_pending")
            continue
        condition = rules[indexes[0]]
        common = lambda value: value.model_dump(exclude={"include_complements"})
        if any(common(value) != common(condition) for value in rules.values()):
            defer(discount, "inconsistent_conditions")
            continue
        value, kind = discount.value, discount.discount_type
        if (kind not in PERCENT_TYPES | AMOUNT_TYPES or value is None
                or not value.is_finite() or value <= 0 or (kind in PERCENT_TYPES and value > 100)):
            defer(discount, "value_or_type_pending")
            continue
        # El MVP define saldo restante y un importe fijo por carrito. Una
        # semántica comercial distinta requiere confirmación, no reinterpretación.
        if ((kind in PERCENT_TYPES and condition.percentage_base == "original")
                or (kind in AMOUNT_TYPES and condition.fixed_scope == "unit")):
            defer(discount, "commercial_compatibility_pending")
            continue
        candidates.append((discount, rules, condition))

    blocked = {}
    for pos, (first, _, first_condition) in enumerate(candidates):
        for second, _, second_condition in candidates[pos + 1:]:
            if (second.discount_id not in first_condition.compatible_discount_ids
                    or first.discount_id not in second_condition.compatible_discount_ids):
                blocked[first.discount_id] = blocked[second.discount_id] = "compatibility_pending"
    for discount, _, _ in candidates:
        if discount.discount_id in blocked:
            defer(discount, blocked[discount.discount_id])
    candidates = [row for row in candidates if row[0].discount_id not in blocked]
    if not candidates:
        return summary.model_copy(update={"pending_discounts": pending,
            "discounts_status": "rules_pending" if pending else "not_applicable"})

    values = [row.subtotal for row in summary.items] + [row[0].value for row in candidates]
    precision = max(value.adjusted() for value in values) + max(0, -min(value.as_tuple().exponent for value in values)) + len(str(len(values))) + 16
    with localcontext() as context:
        context.prec = max(50, precision)
        context.rounding = ROUND_HALF_UP
        original = [[row.selection.base_price * row.quantity, row.selection.complements_total * row.quantity]
                    for row in summary.items]
        # No redondear precios unitarios ni perder fracciones acumuladas por
        # cantidades grandes. Sin una regla adicional, una base fraccionaria
        # no puede conciliar centavos con los detalles históricos exactos.
        if any(money(value) != value for components in original for value in components):
            for discount, _, _ in candidates:
                defer(discount, "price_precision_pending")
            return summary.model_copy(update={"pending_discounts": pending, "discounts_status": "rules_pending"})
        items = [OrderSummaryItem(quantity=row.quantity, selection=row.selection, subtotal=money(row.subtotal))
                 for row in summary.items]
        original = [[money(value) for value in components] for components in original]
        remaining = [list(value) for value in original]
        applied = []
        for discount, rules, condition in sorted(candidates, key=lambda row: (
            row[0].discount_type not in PERCENT_TYPES, row[2].application_order or 0, row[0].discount_id,
        )):
            indexes = sorted(rules)
            balances = [sum(remaining[i][:2 if rules[i].include_complements else 1], ZERO) for i in indexes]
            bases = balances
            if discount.discount_type in PERCENT_TYPES:
                amounts = [min(balance, money(base * discount.value / 100)) for base, balance in zip(bases, balances)]
            else:
                # Una vez por conjunto elegible; el prorrateo sólo distribuye
                # centavos para conservar los saldos de productos y complementos.
                amounts = _allocate(min(sum(balances, ZERO), money(discount.value)), balances)
            allocations = []
            for i, base, amount in zip(indexes, bases, amounts):
                if not amount:
                    continue
                components = remaining[i] if rules[i].include_complements else remaining[i][:1]
                for component, part in enumerate(_allocate(amount, components)):
                    remaining[i][component] -= part
                allocations.append(DiscountAllocation(item_index=i, base=base, amount=amount))
            amount = sum((row.amount for row in allocations), ZERO)
            if amount:
                applied.append(AppliedCommercialDiscount(discount_id=discount.discount_id, name=discount.name,
                    discount_type=discount.discount_type,
                    value=money(discount.value) if discount.discount_type in AMOUNT_TYPES else discount.value,
                    base=sum(bases, ZERO),
                    amount=amount, allocations=allocations))
        if not applied:
            return summary.model_copy(update={"pending_discounts": pending,
                "discounts_status": "rules_pending" if pending else "not_applicable"})
        subtotal = sum((row.subtotal for row in items), ZERO)
        discount_total = sum((row.amount for row in applied), ZERO)
        return OrderSummaryResponse(items=items, subtotal=subtotal, discount_total=discount_total,
            total=subtotal-discount_total, applied_discounts=applied, pending_discounts=pending,
            discounts_status="rules_pending" if pending else "applied")


def apply_discounts(db, summary):
    if not summary.items or not schema_available(db):
        return summary
    rows = db.execute(select(Discount, CommercialDiscountAssignment).outerjoin(
        CommercialDiscountAssignment, Discount.discount_id == CommercialDiscountAssignment.discount_id,
    ).where(Discount.is_active.is_(True)).order_by(Discount.discount_id, CommercialDiscountAssignment.product_id))
    records = {}
    for discount, assignment in rows:
        if discount.discount_id not in records:
            records[discount.discount_id] = (discount, [])
        if assignment is not None:
            records[discount.discount_id][1].append(assignment)
    return calculate(summary, records.values())


def persist_snapshot(db, order_id, summary):
    if schema_available(db):
        db.add(OrderDiscountSnapshot(order_id=order_id, calculation=summary.model_dump(mode="json", include={
            "applied_discounts", "pending_discounts", "discounts_status",
        })))
        db.flush()


def restore_snapshot(order, summary):
    db = object_session(order)
    if db is None or not schema_available(db):
        return summary
    snapshot = db.get(OrderDiscountSnapshot, order.order_id)
    if snapshot is None:
        return summary
    return OrderSummaryResponse.model_validate_json(json.dumps(summary.model_dump(mode="json") | snapshot.calculation))
