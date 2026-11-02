"""Diagrama HU-005 y normalización ORM, sin escrituras en bases de datos."""

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.models import Product, ProductImage, ProductTag, ProductVariant, Tag


@pytest.mark.parametrize("model,columns,primary_key", [
    (ProductImage, {
        "product_image_id": "INTEGER", "product_variant_id": "INTEGER",
        "image_url": "TEXT", "display_order": "INTEGER",
        "created_at": "TIMESTAMP WITH TIME ZONE",
    }, ["product_image_id"]),
    (Tag, {
        "tag_id": "INTEGER", "name": "VARCHAR(150)",
        "created_at": "TIMESTAMP WITH TIME ZONE",
    }, ["tag_id"]),
    (ProductTag, {"product_variant_id": "INTEGER", "tag_id": "INTEGER"},
     ["product_variant_id", "tag_id"]),
])
def test_models_match_approved_diagram(model, columns, primary_key):
    table = model.__table__
    dialect = postgresql.dialect()
    assert {col.name: col.type.compile(dialect=dialect) for col in table.columns} == columns
    assert all(not col.nullable for col in table.columns)
    assert list(table.primary_key.columns.keys()) == primary_key
    assert not any(isinstance(constraint, CheckConstraint) for constraint in table.constraints)
    assert not table.indexes
    for column in table.columns:
        assert column.default is None
        if column.name == "created_at":
            assert str(column.server_default.arg) == "now()"
        else:
            assert column.server_default is None


def test_foreign_keys_point_to_variants_and_tags_without_delete_rules():
    assert {fk.target_fullname for fk in ProductImage.__table__.foreign_keys} == {
        "ProductVariants.product_variant_id",
    }
    assert {fk.target_fullname for fk in ProductTag.__table__.foreign_keys} == {
        "ProductVariants.product_variant_id", "Tags.tag_id",
    }
    for model in (ProductImage, ProductTag):
        assert all(fk.ondelete is None and fk.onupdate is None
                   for fk in model.__table__.foreign_keys)
    assert {tuple(constraint.columns.keys()) for constraint in Tag.__table__.constraints
            if isinstance(constraint, UniqueConstraint)} == {("name",)}


def test_relationships_keep_enrichment_at_variant_level():
    product = Product(name="Producto")
    variant = ProductVariant(name="Presentación", product=product)
    image = ProductImage(variant=variant, image_url="/media/example.webp", display_order=0)
    tag = Tag(name="PICANTE")
    assignment = ProductTag(variant=variant, tag=tag)
    assert image.variant is assignment.variant is variant
    assert image.variant.product is product
    assert tag.variant_assignments == [assignment]
    assert "product_id" not in ProductImage.__table__.columns
    assert "product_id" not in ProductTag.__table__.columns


@pytest.mark.parametrize("value,expected", [
    ("  PICANTE  ", "picante"),
    ("Sin\t  GLUTEN\n", "sin gluten"),
    ("\u00a0VEGETARIANO\u00a0", "vegetariano"),
    ("CAFE\u0301", "café"),
    ("Straße", "strasse"),
    ("\u00df" * 75, "ss" * 75),
])
def test_tag_names_have_a_canonical_form_on_creation_and_rename(value, expected):
    tag = Tag(name=value)
    assert tag.name == expected
    tag.name = "  OTRA  ETIQUETA "
    assert tag.name == "otra etiqueta"


@pytest.mark.parametrize("value", [None, 123, "", " \t\n", "x" * 151, "\u00df" * 76])
def test_invalid_tag_names_are_rejected_before_persistence(value):
    with pytest.raises(ValueError):
        Tag(name=value)
