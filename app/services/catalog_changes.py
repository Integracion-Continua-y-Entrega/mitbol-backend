"""Comparación comercial de solo lectura; nunca simula mediante DML/rollback."""

from sqlalchemy import select

from app.models import CommercialCategory, Product, ProductComplement, ProductModifier, ProductModifierAssignment, ProductVariant
from app.services.catalog_errors import CatalogReferenceError


def _index(db, model, fields):
    columns = [getattr(model, name) for name in ("loyverse_id", *fields)]
    return {row["loyverse_id"]: dict(row) for row in db.execute(select(*columns)).mappings()}


def _compare(existing, desired, retired):
    counts = {"new": 0, "updated": 0, "unchanged": 0, "retired": len(retired),
              "not_returned": len(set(existing) - set(desired) - retired)}
    for identifier, values in desired.items():
        previous = existing.get(identifier)
        if previous is None:
            counts["new"] += 1
        elif any(previous[field] != value for field, value in values.items()):
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def catalog_changes(db, plan) -> dict:
    product_fields = ("product_id", "name", "is_active") + (("category_id",) if plan.categories is not None else ())
    products = _index(db, Product, product_fields)
    categories = _index(db, CommercialCategory, ("category_id", "name", "color", "is_active")) if plan.categories is not None else {}
    category_ids = {row["category_id"]: key for key, row in categories.items()}
    if plan.categories is not None:
        for row in products.values():
            row["category_loyverse_id"] = category_ids.get(row["category_id"])
    variants = _index(db, ProductVariant, ("product_variant_id", "product_id", "name", "price", "is_commercially_available"))
    modifiers = _index(db, ProductModifier, ("product_modifier_id", "name", "display_order"))
    complements = _index(db, ProductComplement, ("product_modifier_id", "name", "price", "display_order", "is_active"))
    product_ids = {row["product_id"]: key for key, row in products.items()}
    variant_ids = {row["product_variant_id"]: key for key, row in variants.items()}
    modifier_ids = {row["product_modifier_id"]: key for key, row in modifiers.items()}

    # La simulación detecta las mismas identidades reasignadas que el UPSERT.
    for row in variants.values():
        row["parent_loyverse_id"] = product_ids[row["product_id"]]
    for row in complements.values():
        row["parent_loyverse_id"] = modifier_ids[row["product_modifier_id"]]
    wanted_products = {row.loyverse_id: {"name": row.name, "is_active": True} for row in plan.products}
    if plan.categories is not None:
        for row in plan.products:
            wanted_products[row.loyverse_id]["category_loyverse_id"] = row.category_loyverse_id
    wanted_variants = {row.loyverse_id: {"name": row.name, "price": row.price,
        "is_commercially_available": row.is_commercially_available, "parent_loyverse_id": row.product_loyverse_id}
        for row in plan.variants}
    wanted_modifiers = {row.loyverse_id: {"name": row.name, "display_order": row.display_order} for row in plan.modifiers}
    wanted_complements = {row.loyverse_id: {"name": row.name, "price": row.price,
        "display_order": row.display_order, "is_active": True, "parent_loyverse_id": row.modifier_loyverse_id}
        for row in plan.complements}
    for existing, desired in ((variants, wanted_variants), (complements, wanted_complements)):
        for identifier, values in desired.items():
            if identifier in existing and existing[identifier]["parent_loyverse_id"] != values["parent_loyverse_id"]:
                raise CatalogReferenceError("Un identificador existente cambió de producto o grupo padre.")
    for existing, tombstones in ((variants, plan.deleted_variants), (complements, plan.deleted_complements)):
        for identifier, parent in tombstones:
            if identifier in existing and existing[identifier]["parent_loyverse_id"] != parent:
                raise CatalogReferenceError("Una baja declara un padre diferente al registrado.")

    retired_products = set(plan.deleted_products) & products.keys()
    retired_modifiers = set(plan.deleted_modifiers) & modifiers.keys()
    retired_variants = ({key for key, row in variants.items() if row["parent_loyverse_id"] in retired_products}
                        | ({key for key, _ in plan.deleted_variants} & variants.keys()))
    retired_complements = ({key for key, row in complements.items() if row["parent_loyverse_id"] in retired_modifiers}
                           | ({key for key, _ in plan.deleted_complements} & complements.keys()))
    current_links = {(variant_ids[variant], modifier_ids[modifier]) for variant, modifier in db.execute(select(
        ProductModifierAssignment.product_variant_id, ProductModifierAssignment.product_modifier_id,
    ))}
    replaced_variants = set(wanted_variants) | retired_variants
    desired_links = {link for link in current_links if link[0] not in replaced_variants and link[1] not in retired_modifiers}
    desired_links.update(plan.assignments)
    result = {
        "products": _compare(products, wanted_products, retired_products),
        "variants": _compare(variants, wanted_variants, retired_variants),
        "modifiers": _compare(modifiers, wanted_modifiers, retired_modifiers),
        "complements": _compare(complements, wanted_complements, retired_complements),
        "assignments": {"inserted": len(desired_links - current_links), "removed": len(current_links - desired_links)},
    }
    if plan.categories is not None:
        desired = {row.loyverse_id: {"name": row.name, "color": row.color, "is_active": True} for row in plan.categories}
        result["categories"] = _compare(categories, desired, set(plan.deleted_categories) & categories.keys())
    return result
