"""Plan comercial validado; no abre conexiones ni modifica objetos del snapshot."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.services.catalog_errors import CatalogValidationError
from app.services.catalog_probe import (
    CatalogSnapshot, _article_modifier_ids, _identities, _list, _text, describe_catalog,
)
from app.services.loyverse_client import LoyverseResponseError


@dataclass(frozen=True)
class ProductData:
    loyverse_id: str
    name: str


@dataclass(frozen=True)
class VariantData:
    loyverse_id: str
    product_loyverse_id: str
    name: str
    price: Decimal
    is_commercially_available: bool


@dataclass(frozen=True)
class ModifierData:
    loyverse_id: str
    name: str
    display_order: int


@dataclass(frozen=True)
class ComplementData:
    loyverse_id: str
    modifier_loyverse_id: str
    name: str
    price: Decimal
    display_order: int


@dataclass(frozen=True)
class CatalogPlan:
    store_id: str
    synced_at: datetime
    products: tuple[ProductData, ...]
    variants: tuple[VariantData, ...]
    modifiers: tuple[ModifierData, ...]
    complements: tuple[ComplementData, ...]
    assignments: tuple[tuple[str, str], ...]
    deleted_products: tuple[str, ...]
    deleted_variants: tuple[tuple[str, str], ...]
    deleted_modifiers: tuple[str, ...]
    deleted_complements: tuple[tuple[str, str], ...]


def _bounded(value: str, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CatalogValidationError(f"{label} no cabe en el campo definido por el modelo.")
    return value


def _identifier(value: str) -> str:
    return _bounded(value, 100, "Identificador Loyverse")


def _name(value: str) -> str:
    return _bounded(value, 150, "Nombre comercial")


def _price(value: str | None) -> Decimal:
    try:
        price = Decimal(value) if isinstance(value, str) else None
    except InvalidOperation:
        price = None
    if price is None or not price.is_finite() or price < 0:
        raise CatalogValidationError("Precio desconocido, negativo o no finito; no se inventa un importe.")
    return price


def _order(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -(2**31) <= value < 2**31:
        raise CatalogValidationError("El orden comercial no cabe en un INTEGER PostgreSQL.")
    return value


def _deleted(record: dict) -> bool:
    value = record.get("deleted_at")
    if value is None:
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError, TypeError):
        raise CatalogValidationError("deleted_at debe ser una fecha válida con zona horaria.") from None
    if timestamp.utcoffset() is None:
        raise CatalogValidationError("deleted_at debe incluir zona horaria.")
    return True


def transform_catalog(snapshot: CatalogSnapshot) -> CatalogPlan:
    try:
        return _transform_catalog(snapshot)
    except LoyverseResponseError as error:
        raise CatalogValidationError(str(error)) from error


def _transform_catalog(snapshot: CatalogSnapshot) -> CatalogPlan:
    if not snapshot.includes_deleted:
        raise CatalogValidationError("La persistencia requiere una lectura con show_deleted=true.")
    if snapshot.fetched_at.utcoffset() is None:
        raise CatalogValidationError("La fecha de sincronización debe incluir zona horaria.")
    store_id = _identifier(_text(snapshot.store, "id"))
    items = _identities(list(snapshot.items))
    groups = _identities(list(snapshot.modifiers))
    deleted_products = []
    deleted_variants = []
    deleted_modifiers = []
    deleted_complements = []
    active_groups = []
    active_items = []
    seen_variants = set()
    seen_options = set()

    for group_id, group in groups.items():
        _identifier(group_id)
        if _deleted(group):
            # Un tombstone puede traer sólo id y deleted_at. No exige un precio.
            deleted_modifiers.append(group_id)
            continue
        store_ids = _list(group, "stores")
        if any(not isinstance(value, str) or not value for value in store_ids):
            raise CatalogValidationError("Un grupo declara tiendas inválidas.")
        if store_id not in store_ids:
            # No se importa la configuración de otro establecimiento.
            continue
        options = []
        for option_id, option in _identities(_list(group, "modifier_options")).items():
            _identifier(option_id)
            if option_id in seen_options:
                raise CatalogValidationError("Una opción pertenece a más de un grupo.")
            seen_options.add(option_id)
            if _deleted(option):
                deleted_complements.append((option_id, group_id))
            else:
                options.append(option)
        active_groups.append({**group, "modifier_options": options})

    for item_id, item in items.items():
        _identifier(item_id)
        if _deleted(item):
            deleted_products.append(item_id)
            continue
        modifier_field, modifier_ids = _article_modifier_ids(item)
        allowed_modifiers = [value for value in modifier_ids if value not in deleted_modifiers]
        item_copy = {**item, modifier_field: allowed_modifiers}
        # Cuando existen los dos alias coherentes, se conservan coherentes.
        for alias in ("modifier_ids", "modifiers_ids"):
            if alias in item_copy:
                item_copy[alias] = allowed_modifiers
        variants = []
        for variant_id, variant in _identities(_list(item, "variants"), "variant_id").items():
            _identifier(variant_id)
            if variant_id in seen_variants:
                raise CatalogValidationError("Una variante pertenece a más de un artículo.")
            seen_variants.add(variant_id)
            if _text(variant, "item_id") != item_id:
                raise CatalogValidationError("La variante no corresponde a su artículo.")
            if _deleted(variant):
                deleted_variants.append((variant_id, item_id))
            else:
                variants.append(variant)
        if not variants:
            # Un artículo con todas sus variantes explícitamente dadas de baja
            # conserva su identidad, pero no tiene presentaciones solicitables.
            if not _list(item, "variants"):
                raise CatalogValidationError("Un artículo activo no incluye sus variantes.")
        active_items.append({**item_copy, "variants": variants})

    active_snapshot = CatalogSnapshot(
        snapshot.store, tuple(active_items), tuple(active_groups), True, snapshot.fetched_at,
    )
    report = describe_catalog(active_snapshot)
    blocking = [issue for issue in report["issues"] if issue["code"] != "item_without_variants"]
    if blocking:
        codes = ", ".join(sorted({issue["code"] for issue in blocking}))
        raise CatalogValidationError(f"Configuración comercial incompatible: {codes}.")

    products = []
    variants = []
    modifiers = []
    complements = []
    assignments = []
    for item in report["items"]:
        name = _name(item["name"])
        products.append(ProductData(_identifier(item["id"]), name))
        for variant in item["variants"]:
            values = [value for value in variant["option_values"] if value is not None]
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise CatalogValidationError("La variante incluye un valor de opción inválido.")
            # La API no tiene un campo name de variante: se deriva de sus valores
            # reales; una variante sin opciones usa el nombre de su producto.
            variant_name = _name(" / ".join(values) if values else name)
            variants.append(VariantData(
                variant["id"], item["id"], variant_name,
                _price(variant["store_price"]), variant["available_for_sale"],
            ))
            assignments.extend((variant["id"], modifier_id) for modifier_id in item["modifier_ids"])
    for group in report["modifiers"]:
        modifiers.append(ModifierData(group["id"], _name(group["name"]), _order(group["position"])))
        for option in group["options"]:
            complements.append(ComplementData(
                option["id"], group["id"], _name(option["name"]),
                _price(option["price"]), _order(option["position"]),
            ))
    return CatalogPlan(
        store_id, snapshot.fetched_at,
        tuple(products), tuple(variants), tuple(modifiers), tuple(complements), tuple(assignments),
        tuple(deleted_products), tuple(deleted_variants), tuple(deleted_modifiers), tuple(deleted_complements),
    )
