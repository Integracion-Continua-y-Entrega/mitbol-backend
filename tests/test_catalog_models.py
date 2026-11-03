"""Integridad del catálogo en PostgreSQL aislado."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Base, Product, ProductComplement, ProductModifier,
    ProductModifierAssignment, ProductVariant,
)


@pytest.fixture
def catalog_session(sync_db):
    with sync_db.sessions() as session:
        product = Product(
            loyverse_id="item-1", name="Mitbol", description="Descripción local",
            is_available=False, is_featured=True,
            synced_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        )
        variant = ProductVariant(
            loyverse_id="variant-1", name="Grande", price=Decimal("123.45"),
            synced_at=product.synced_at, product=product,
        )
        modifier = ProductModifier(
            loyverse_id="modifier-1", name="Extras", display_order=1,
        )
        complement = ProductComplement(
            loyverse_id="option-1", name="Queso", price=Decimal("12.50"),
            synced_at=product.synced_at, display_order=1, modifier=modifier,
        )
        assignment = ProductModifierAssignment(variant=variant, modifier=modifier)
        session.add_all([product, variant, modifier, complement, assignment])
        session.commit()
        yield session


def test_catalog_relationships_and_decimal_prices(catalog_session):
    product = catalog_session.scalars(select(Product)).one()
    variant = product.variants[0]
    modifier = variant.modifier_assignments[0].modifier
    assert variant.product is product
    assert variant.price == Decimal("123.45")
    assert isinstance(variant.price, Decimal)
    assert modifier.complements[0].price == Decimal("12.50")
    assert modifier.variant_assignments[0].variant is variant
    assert product.description == "Descripción local"
    assert product.is_available is False
    assert product.is_featured is True
    assert product.is_active is True
    assert product.is_new is True
    assert variant.is_available is True
    assert variant.is_commercially_available is False
    assert modifier.synced_at is None
    assert modifier.complements[0].is_active is True


@pytest.mark.parametrize(
    "model,loyverse_id,extra",
    [
        (Product, "item-1", {}),
        (ProductVariant, "variant-1", {"product_id": 1, "price": Decimal("1.00")}),
        (ProductModifier, "modifier-1", {"display_order": 1}),
        (ProductComplement, "option-1", {
            "product_modifier_id": 1, "price": Decimal("1.00"), "display_order": 1,
        }),
    ],
)
def test_duplicate_loyverse_id_is_rejected(catalog_session, model, loyverse_id, extra):
    extra = dict(extra)
    if "product_id" in extra:
        extra["product_id"] = catalog_session.scalar(select(Product.product_id))
    if "product_modifier_id" in extra:
        extra["product_modifier_id"] = catalog_session.scalar(select(ProductModifier.product_modifier_id))
    values = {
        "loyverse_id": loyverse_id, "name": "Duplicado",
        "synced_at": datetime(2026, 10, 29, tzinfo=timezone.utc), **extra,
    }
    with pytest.raises(IntegrityError) as error:
        catalog_session.execute(insert(model).values(**values))
    assert error.value.orig.pgcode == "23505"
    catalog_session.rollback()


def test_duplicate_modifier_assignment_is_rejected(catalog_session):
    assignment = catalog_session.scalars(select(ProductModifierAssignment)).one()
    with pytest.raises(IntegrityError):
        catalog_session.execute(insert(ProductModifierAssignment).values(
            product_variant_id=assignment.product_variant_id,
            product_modifier_id=assignment.product_modifier_id,
        ))
    catalog_session.rollback()


def test_variant_requires_existing_product(catalog_session):
    with pytest.raises(IntegrityError):
        catalog_session.execute(insert(ProductVariant).values(
            loyverse_id="orphan", name="Sin producto", price=Decimal("1.00"),
            synced_at=datetime(2026, 10, 29, tzinfo=timezone.utc), product_id=999,
        ))
    catalog_session.rollback()


@pytest.mark.parametrize("model,extra", [
    (ProductVariant, {"product_id": 1}),
    (ProductComplement, {"product_modifier_id": 1, "display_order": 2}),
])
def test_null_price_is_rejected(catalog_session, model, extra):
    extra = dict(extra)
    if "product_id" in extra:
        extra["product_id"] = catalog_session.scalar(select(Product.product_id))
    if "product_modifier_id" in extra:
        extra["product_modifier_id"] = catalog_session.scalar(select(ProductModifier.product_modifier_id))
    with pytest.raises(IntegrityError) as error:
        catalog_session.execute(insert(model).values(
            loyverse_id="no-price", name="Sin precio", price=None,
            synced_at=datetime(2026, 10, 29, tzinfo=timezone.utc), **extra,
        ))
    assert error.value.orig.pgcode == "23502"
    catalog_session.rollback()


@pytest.mark.parametrize("parent_model", [Product, ProductModifier])
def test_parent_deletion_does_not_cascade(catalog_session, parent_model):
    parent = catalog_session.scalars(select(parent_model)).one()
    catalog_session.delete(parent)
    with pytest.raises(IntegrityError):
        catalog_session.commit()
    catalog_session.rollback()
    assert catalog_session.scalars(select(ProductVariant)).one().loyverse_id == "variant-1"
    assert catalog_session.scalars(select(ProductComplement)).one().loyverse_id == "option-1"
