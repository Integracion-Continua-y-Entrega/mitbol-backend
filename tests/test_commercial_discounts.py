"""HU-019: reglas sintéticas explícitas; nunca promociones reales del restaurante."""

from decimal import Decimal, ROUND_DOWN, localcontext
from types import SimpleNamespace

import pytest

from app.schemas.order_summary import OrderSummaryItem, OrderSummaryResponse
from app.schemas.product_options import ProductSelectionResponse, SelectedComplement
from app.services.commercial_discount_service import calculate


def summary(*rows):
    items = []
    for product_id, base, extra, quantity in rows:
        base, extra = Decimal(base), Decimal(extra)
        options = [SelectedComplement(product_modifier_id=1, product_complement_id=1, name="Extra", price=extra)] if extra else []
        selection = ProductSelectionResponse(product_id=product_id, product_variant_id=product_id,
            product_name=f"Producto {product_id}", variant_name="Única", base_price=base, complements_total=extra,
            complements=options, unit_total=base+extra)
        items.append(OrderSummaryItem(quantity=quantity, selection=selection, subtotal=(base+extra)*quantity))
    subtotal = sum((row.subtotal for row in items), Decimal("0"))
    return OrderSummaryResponse(items=items, subtotal=subtotal, total=subtotal)


def conditions(**changes):
    return {"include_complements": False, "compatible_discount_ids": [], "percentage_base": "remaining",
            "fixed_scope": "eligible_subtotal", "fixed_allocation": "proportional", "application_order": 0} | changes


def discount(identifier=1, kind="FIXED_PERCENT", value="10", *, products=(1,), rules=None, active=True):
    record = SimpleNamespace(discount_id=identifier, name=f"Descuento sintético {identifier}",
        discount_type=kind, value=Decimal(value) if value is not None else None, is_active=active)
    return record, [SimpleNamespace(product_id=product_id, conditions=conditions() if rules is None else rules)
                    for product_id in products]


@pytest.mark.parametrize("include,amount,total", [(False,"10.00","140.00"),(True,"12.00","138.00")])
def test_percentage_only_eligible_products_and_explicit_complements(include, amount, total):
    result = calculate(summary((1,"50","10",2),(2,"30","0",1)),
                       [discount(rules=conditions(include_complements=include))])
    assert result.discount_total == Decimal(amount) and result.total == Decimal(total)
    applied, = result.applied_discounts
    assert applied.allocations[0].item_index == 0 and len(applied.allocations) == 1
    assert result.subtotal == sum(row.subtotal for row in result.items) == Decimal("150.00")


@pytest.mark.parametrize("explicit_mvp_conditions", [False, True])
def test_compatible_percentages_then_fixed_even_when_records_arrive_out_of_order(explicit_mvp_conditions):
    def rules(compatible, order):
        required = {"include_complements":False,"compatible_discount_ids":compatible}
        return conditions(**required,application_order=order) if explicit_mvp_conditions else required

    records = [discount(1,value="20",rules=rules([2,3],0)),
               discount(2,value="10",rules=rules([1,3],1)),
               discount(3,"FIXED_AMOUNT","5",rules=rules([1,2],0))]
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        result = calculate(summary((1,"100","0",1)), reversed(records))
        assert context.prec == 2
        assert context.rounding == ROUND_DOWN
    assert result.total == Decimal("67.00")
    assert [row.discount_id for row in result.applied_discounts] == [1,2,3]
    assert [row.base for row in result.applied_discounts] == [Decimal("100.00"),Decimal("80.00"),Decimal("72.00")]
    assert [row.amount for row in result.applied_discounts] == [Decimal("20.00"),Decimal("8.00"),Decimal("5.00")]
    assert result.discount_total == sum(row.amount for row in result.applied_discounts)


def test_multiple_compatible_fixed_discounts_cap_only_eligible_balances():
    result = calculate(summary((1,"10","5",2),(2,"50","0",1)), [
        discount(1,"FIXED_AMOUNT","25",rules=conditions(compatible_discount_ids=[2],application_order=0)),
        discount(2,"FIXED_AMOUNT","100",rules=conditions(compatible_discount_ids=[1],application_order=1)),
    ])
    assert result.discount_total == Decimal("20.00") and result.total == Decimal("60.00")
    assert len(result.applied_discounts) == 1


def test_percentage_then_fixed_preserves_complements_when_excluded_and_can_discount_them_explicitly():
    records = [discount(1,value="100",rules=conditions(compatible_discount_ids=[2])),
               discount(2,"FIXED_AMOUNT","999",rules=conditions(compatible_discount_ids=[1],include_complements=True))]
    result = calculate(summary((1,"10","5",2)), records)
    assert [row.amount for row in result.applied_discounts] == [Decimal("20.00"),Decimal("10.00")]
    assert result.total == 0 and result.discount_total == result.subtotal


@pytest.mark.parametrize("kind,value,base,expected", [
    ("FIXED_PERCENT","10","0.05","0.01"),
    ("FIXED_PERCENT","50","0.03","0.02"),
    ("FIXED_AMOUNT","0.005","10.00","0.01"),
    ("VARIABLE_PERCENT","10","0.05","0.01"),
    ("VARIABLE_AMOUNT","2","10","2.00"),
])
def test_round_half_up_and_variables_require_an_actual_backend_value(kind, value, base, expected):
    result = calculate(summary((1,base,"0",1)), [discount(kind=kind,value=value)])
    assert result.discount_total == Decimal(expected)
    assert result.discount_total.as_tuple().exponent == -2


def test_fixed_prorating_balances_cents_without_negative_lines():
    result = calculate(summary((1,"0.01","0",1),(2,"0.01","0",1),(3,"0.01","0",1)),
                       [discount(kind="FIXED_AMOUNT",value="0.02",products=(1,2,3))])
    applied, = result.applied_discounts
    assert result.total == Decimal("0.01")
    assert [(a.item_index,a.amount) for a in applied.allocations] == [(0,Decimal("0.01")),(1,Decimal("0.01"))]


def test_fixed_amount_applies_once_to_the_eligible_cart_without_extra_commercial_parameters():
    result = calculate(summary((1,"10","2",3),(2,"20","0",2)),
        [discount(kind="FIXED_AMOUNT",value="5",products=(1,2),
                  rules={"include_complements":False,"compatible_discount_ids":[]})])
    assert result.discount_total == Decimal("5.00") and result.total == Decimal("71.00")
    applied, = result.applied_discounts
    assert applied.value == Decimal("5.00") and applied.base == Decimal("70.00")
    assert sum(row.amount for row in applied.allocations) == Decimal("5.00")


@pytest.mark.parametrize("kind,change", [
    ("FIXED_PERCENT",{"percentage_base":"original"}),
    ("FIXED_AMOUNT",{"fixed_scope":"unit"}),
])
def test_commercial_semantics_that_differ_from_the_mvp_are_pending_not_reinterpreted(kind, change):
    result = calculate(summary((1,"100","0",2)), [discount(kind=kind,rules=conditions(**change))])
    assert result.total == Decimal("200") and not result.applied_discounts
    assert result.pending_discounts[0].reason == "commercial_compatibility_pending"


@pytest.mark.parametrize("kind,value", [("VARIABLE_PERCENT",None),("VARIABLE_AMOUNT",None),
    ("DISCOUNT_BY_POINTS",None),("UNRECOGNIZED","10"),("FIXED_PERCENT","101"),
    ("FIXED_AMOUNT","NaN"),("FIXED_AMOUNT","Infinity"),("FIXED_AMOUNT","-1")])
def test_insufficient_or_invalid_benefits_never_change_totals(kind, value):
    original = summary((1,"10.1234","0",1))
    result = calculate(original, [discount(kind=kind,value=value)])
    assert result.total == original.total and result.items == original.items
    assert result.discount_total == 0 and result.discounts_status == "rules_pending"


@pytest.mark.parametrize("rules", [None,{}, {"include_complements":False},
    conditions(include_complements="true"), conditions(unknown_condition=True)])
def test_missing_and_unknown_conditions_do_not_enable_a_discount(rules):
    record, associations = discount()
    associations[0].conditions = rules
    result = calculate(summary((1,"100","0",1)), [(record,associations)])
    assert result.total == 100 and result.pending_discounts[0].reason == "conditions_pending"


def test_unassigned_and_other_product_and_inactive_discounts_are_not_applied():
    result = calculate(summary((1,"100","0",1)), [discount(products=()),discount(2,products=(2,)),discount(3,active=False)])
    assert result.total == 100 and not result.applied_discounts
    assert [(row.discount_id,row.reason) for row in result.pending_discounts] == [(1,"eligibility_pending")]


def test_stacking_still_requires_explicit_mutual_compatibility():
    first = discount(1,rules=conditions(compatible_discount_ids=[2],application_order=0))
    second = discount(2,rules=conditions(compatible_discount_ids=[],application_order=1))
    result = calculate(summary((1,"100","0",1)), [first,second])
    assert result.total == 100 and not result.applied_discounts
    assert {row.reason for row in result.pending_discounts} == {"compatibility_pending"}


@pytest.mark.parametrize("order", [None, 0])
def test_missing_or_tied_order_uses_discount_id_and_remaining_base_deterministically(order):
    records = [discount(1,value="10",rules=conditions(compatible_discount_ids=[2],application_order=order,percentage_base=None)),
               discount(2,value="20",rules=conditions(compatible_discount_ids=[1],application_order=order,percentage_base=None))]
    original = summary((1,"0.05","0",1))
    result = calculate(original,reversed(records))
    assert result.model_dump() == calculate(original,records).model_dump()
    assert result.total == Decimal("0.03") and result.discounts_status == "applied"
    assert [row.discount_id for row in result.applied_discounts] == [1,2]


def test_fractional_cent_bases_preserve_prices_and_report_pending_rounding_rules():
    original = summary((1,"0.335","0.335",3))
    assert calculate(original, []).total == original.total
    result = calculate(original, [discount(rules=conditions(include_complements=True))])
    assert result.subtotal == result.total == original.subtotal and result.discount_total == 0
    assert result.items == original.items
    assert result.pending_discounts[0].reason == "price_precision_pending"


def test_fractional_unit_prices_with_exact_cent_line_bases_do_not_get_rounded_away():
    original = summary((1,"0.0001","0",10000),(2,"10","0",1))
    result = calculate(original,[discount(products=(2,))])
    assert result.subtotal == Decimal("11.00") and result.discount_total == Decimal("1.00")
    assert result.total == Decimal("10.00")
    assert result.items[0].selection.base_price == Decimal("0.0001")


def test_ineligible_fractional_cent_line_cannot_create_an_inconsistent_rounded_total():
    original = summary((1,"10","0",1),(2,"0.0001","0",1))
    result = calculate(original,[discount()])
    assert result.total == original.total and not result.applied_discounts
    assert result.pending_discounts[0].reason == "price_precision_pending"
