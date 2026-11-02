"""Diagnóstico del catálogo remoto. No importa SQLAlchemy ni escribe en bases."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.services.loyverse_client import (
    LoyverseAPIClient, LoyverseConfigurationError, LoyverseResponseError,
)


@dataclass(frozen=True)
class CatalogSnapshot:
    store: dict
    items: tuple[dict, ...]
    modifiers: tuple[dict, ...]
    includes_deleted: bool = False
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def fetch_catalog(
    client: LoyverseAPIClient, store_id: str | None, *, include_deleted: bool = False,
) -> CatalogSnapshot:
    store = select_store(await client.get_stores(), store_id)
    items = await client.get_items(include_deleted=include_deleted)
    modifiers = await client.get_modifiers(include_deleted=include_deleted)
    # El cliente sólo entrega una colección cuando terminó toda su paginación.
    return CatalogSnapshot(store, tuple(items), tuple(modifiers), include_deleted)


def _text(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LoyverseResponseError(f"Falta el campo de texto '{field}' en el catálogo.")
    return value


def _list(record: dict, field: str, *, nullable=False) -> list:
    if field not in record:
        raise LoyverseResponseError(f"Falta el campo '{field}' en el catálogo.")
    value = record[field]
    if value is None and nullable:
        return []
    if not isinstance(value, list):
        raise LoyverseResponseError(f"El campo '{field}' debe ser una lista.")
    return value


def _identities(records: list, field="id") -> dict[str, dict]:
    indexed = {}
    for record in records:
        if not isinstance(record, dict):
            raise LoyverseResponseError("El catálogo contiene un registro inválido.")
        identifier = _text(record, field)
        if identifier in indexed:
            raise LoyverseResponseError(f"Identificador duplicado en '{field}'.")
        indexed[identifier] = record
    return indexed


def _money(record: dict, field: str) -> str | None:
    if field not in record:
        raise LoyverseResponseError(f"Falta el importe '{field}' en el catálogo.")
    value = record[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise LoyverseResponseError(f"El importe '{field}' no es un número válido.")
    if not Decimal(value).is_finite():
        raise LoyverseResponseError(f"El importe '{field}' no es finito.")
    # Se serializa como texto para conservar el decimal al generar el informe.
    return str(value)


def _position(record: dict) -> int:
    value = record.get("position")
    if isinstance(value, bool) or not isinstance(value, int):
        raise LoyverseResponseError("Falta una posición válida en un modificador u opción.")
    return value


def _article_modifier_ids(item: dict) -> tuple[str, list[str]]:
    # La respuesta real usa modifier_ids; la referencia publicada muestra
    # modifiers_ids. Se admiten sólo esas dos formas comprobadas.
    fields = [field for field in ("modifier_ids", "modifiers_ids") if field in item]
    if not fields:
        raise LoyverseResponseError("Falta la relación de modificadores del artículo.")
    if len(fields) == 2 and item[fields[0]] != item[fields[1]]:
        raise LoyverseResponseError("El artículo declara dos relaciones de modificadores contradictorias.")
    field = fields[0]
    identifiers = _list(item, field, nullable=True)
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise LoyverseResponseError("Un artículo declara identificadores de modificador inválidos.")
    if len(set(identifiers)) != len(identifiers):
        raise LoyverseResponseError("Un artículo declara el mismo modificador varias veces.")
    return field, identifiers


def select_store(stores: list[dict], store_id: str | None) -> dict:
    indexed = _identities(stores)
    active = {key: store for key, store in indexed.items() if not store.get("deleted_at")}
    if store_id:
        if store_id not in active:
            raise LoyverseConfigurationError("LOYVERSE_STORE_ID no corresponde a una tienda activa accesible.")
        selected = active[store_id]
    elif len(active) == 1:
        selected = next(iter(active.values()))
    else:
        raise LoyverseConfigurationError(
            "Se requiere LOYVERSE_STORE_ID: no hay una única tienda activa accesible."
        )
    return {
        "id": _text(selected, "id"), "name": _text(selected, "name"),
        "selection": "configured_id" if store_id else "only_active_store",
    }


def _inspect_variant(variant: dict, item_id: str, store_id: str, issues: list) -> dict:
    variant_id = _text(variant, "variant_id")
    if _text(variant, "item_id") != item_id:
        raise LoyverseResponseError("Una variante declara un artículo diferente al que la contiene.")
    default_type = _text(variant, "default_pricing_type")
    default_price = _money(variant, "default_price")
    stores = _identities(_list(variant, "stores"), "store_id")
    selected = stores.get(store_id)
    pricing_type = None
    store_price = None
    available = None
    price_source = None
    if selected is None:
        issues.append({"code": "missing_store_configuration", "entity_id": variant_id})
    else:
        pricing_type = _text(selected, "pricing_type")
        store_price = _money(selected, "price")
        available = selected.get("available_for_sale")
        if not isinstance(available, bool):
            raise LoyverseResponseError("Falta available_for_sale en la variante de la tienda.")
        if pricing_type == "VARIABLE":
            issues.append({"code": "variable_price", "entity_id": variant_id})
        elif pricing_type != "FIXED":
            issues.append({"code": "unsupported_pricing_type", "entity_id": variant_id})
        elif store_price is None:
            issues.append({"code": "unknown_store_price", "entity_id": variant_id})
        else:
            price_source = "variants[].stores[].price"
    return {
        "id": variant_id,
        "option_values": [variant.get(f"option{i}_value") for i in (1, 2, 3)],
        "default_pricing_type": default_type, "default_price": default_price,
        "store_pricing_type": pricing_type, "store_price": store_price,
        "price_source": price_source, "available_for_sale": available,
        "store_price_differs_from_default": (
            Decimal(store_price) != Decimal(default_price)
            if store_price is not None and default_price is not None else None
        ),
    }


async def inspect_catalog(client: LoyverseAPIClient, store_id: str | None = None) -> dict:
    """Consulta tres endpoints y describe datos observados, sin generar un UPSERT."""
    snapshot = await fetch_catalog(client, store_id)
    report = describe_catalog(snapshot)
    report.update(http_requests=client.request_count, pages_received=dict(client.page_counts))
    return report


def describe_catalog(snapshot: CatalogSnapshot) -> dict:
    """Inspección reutilizable en memoria; no realiza HTTP ni persistencia."""
    store = snapshot.store
    items = _identities(list(snapshot.items))
    modifiers = _identities(list(snapshot.modifiers))
    issues = []
    groups = []
    seen_option_ids = set()
    for identifier, modifier in modifiers.items():
        store_ids = _list(modifier, "stores")
        if any(not isinstance(value, str) or not value for value in store_ids):
            raise LoyverseResponseError("El grupo contiene identificadores de tienda inválidos.")
        options = []
        for option_id, option in _identities(_list(modifier, "modifier_options")).items():
            if option_id in seen_option_ids:
                raise LoyverseResponseError("Una opción aparece en más de un grupo.")
            seen_option_ids.add(option_id)
            price = _money(option, "price")
            if price is None:
                issues.append({"code": "unknown_modifier_option_price", "entity_id": option_id})
            options.append({
                "id": option_id, "name": _text(option, "name"),
                "price": price, "position": _position(option),
                "deleted_at": option.get("deleted_at"),
            })
        groups.append({
            "id": identifier, "name": _text(modifier, "name"),
            "position": _position(modifier), "store_ids": store_ids,
            "options": options,
        })

    articles = []
    seen_variant_ids = set()
    used_modifier_ids = set()
    for identifier, item in items.items():
        modifier_field, modifier_ids = _article_modifier_ids(item)
        used_modifier_ids.update(modifier_ids)
        for modifier_id in modifier_ids:
            if modifier_id not in modifiers:
                issues.append({"code": "missing_modifier", "entity_id": identifier, "modifier_id": modifier_id})
            elif store["id"] not in modifiers[modifier_id]["stores"]:
                issues.append({"code": "modifier_not_in_store", "entity_id": identifier, "modifier_id": modifier_id})
        variants = _identities(_list(item, "variants"), "variant_id")
        if not variants:
            issues.append({"code": "item_without_variants", "entity_id": identifier})
        observed_variants = []
        for variant_id, variant in variants.items():
            if variant_id in seen_variant_ids:
                raise LoyverseResponseError("Una variante aparece en más de un artículo.")
            seen_variant_ids.add(variant_id)
            observed_variants.append(_inspect_variant(variant, identifier, store["id"], issues))
        articles.append({
            "id": identifier, "name": _text(item, "item_name"),
            "modifier_ids_field": modifier_field,
            "modifier_ids": modifier_ids, "variants": observed_variants,
        })

    return {
        "checked_at_utc": snapshot.fetched_at.isoformat(),
        "mode": "read_only", "store": store,
        "counts": {
            "items": len(items), "variants": len(seen_variant_ids),
            "modifier_groups": len(groups), "modifier_options": len(seen_option_ids),
            "article_modifier_links": sum(len(item["modifier_ids"]) for item in articles),
        },
        "items": articles, "modifiers": groups, "issues": issues,
        "unused_modifier_ids": sorted(set(modifiers) - used_modifier_ids),
        "deleted_records_requested": snapshot.includes_deleted,
    }
