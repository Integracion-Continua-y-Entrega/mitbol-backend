"""Valida descuentos completos antes de persistir; no realiza E/S ni calcula beneficios."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from app.services.discount_rules import DiscountType, is_variable_discount


class DiscountValidationError(ValueError):
    """Respuesta comercial inválida; el mensaje no incluye payload ni credenciales."""


@dataclass(frozen=True)
class DiscountRecord:
    loyverse_id: str
    name: str
    discount_type: DiscountType
    value: Decimal | None
    stores: tuple[str, ...]
    restricted_access: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @property
    def is_variable(self) -> bool:
        return is_variable_discount(self.discount_type)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def _identifier(value: object) -> str:
    if not isinstance(value, str):
        raise DiscountValidationError("Identificador de descuento o tienda inválido.")
    try:
        return str(UUID(value))
    except ValueError:
        raise DiscountValidationError("Identificador de descuento o tienda inválido.") from None


def _timestamp(value: object, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise DiscountValidationError("Fecha de descuento ausente o inválida.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise DiscountValidationError("Fecha de descuento ausente o inválida.") from None


def _fixed_value(value: object, maximum: Decimal) -> Decimal:
    # El cliente HTTP ya decodifica los números JSON con Decimal.
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise DiscountValidationError("Valor fijo de descuento inválido.")
    amount = Decimal(value)
    if not amount.is_finite() or not Decimal("0.01") <= amount <= maximum:
        raise DiscountValidationError("Valor fijo de descuento fuera del rango del proveedor.")
    return amount


def parse_discount(record: dict) -> DiscountRecord:
    if not isinstance(record, dict):
        raise DiscountValidationError("Se esperaba un objeto de descuento.")
    identifier = _identifier(record.get("id"))
    name = record.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 40:
        raise DiscountValidationError("Nombre de descuento inválido.")
    try:
        kind = DiscountType(record.get("type"))
    except (ValueError, TypeError):
        raise DiscountValidationError("Tipo de descuento no reconocido.") from None

    if kind is DiscountType.FIXED_PERCENT:
        value = _fixed_value(record.get("discount_percent"), Decimal("100"))
    elif kind is DiscountType.FIXED_AMOUNT:
        value = _fixed_value(record.get("discount_amount"), Decimal("999999.99"))
    else:
        # Variables y puntos no tienen un valor fijo de catálogo. No calcularlo.
        if record.get("discount_percent") is not None or record.get("discount_amount") is not None:
            raise DiscountValidationError("Tipo no fijo con un valor fijo inesperado.")
        value = None

    stores = record.get("stores")
    if not isinstance(stores, list):
        raise DiscountValidationError("Lista de tiendas ausente o inválida.")
    store_ids = tuple(_identifier(store) for store in stores)
    if len(set(store_ids)) != len(store_ids):
        raise DiscountValidationError("Tiendas duplicadas en el descuento.")
    restricted = record.get("restricted_access")
    if not isinstance(restricted, bool):
        raise DiscountValidationError("Restricción de acceso ausente o inválida.")

    created_at = _timestamp(record.get("created_at"))
    updated_at = _timestamp(record.get("updated_at"))
    assert created_at is not None and updated_at is not None
    return DiscountRecord(
        loyverse_id=identifier, name=name, discount_type=kind, value=value,
        stores=store_ids, restricted_access=restricted,
        created_at=created_at,
        updated_at=updated_at,
        deleted_at=_timestamp(record.get("deleted_at"), nullable=True),
    )


def parse_discount_records(records: list[dict]) -> tuple[DiscountRecord, ...]:
    """Entrega el lote solo tras validarlo completo; no modifica el argumento."""
    if not isinstance(records, list):
        raise DiscountValidationError("Se esperaba una lista de descuentos.")
    result = []
    seen = set()
    for record in records:
        parsed = parse_discount(record)
        if parsed.loyverse_id in seen:
            raise DiscountValidationError("Identificador de descuento duplicado en la respuesta.")
        seen.add(parsed.loyverse_id)
        result.append(parsed)
    return tuple(result)


def discount_applies_to_store(record: DiscountRecord, store_id: str) -> bool:
    """Regla acordada: lista vacía universal; IDs explícitos limitan las tiendas.

    Esto no determina scope ni activación: las bajas se resuelven en el mapeo.
    """
    identifier = _identifier(store_id)
    return not record.stores or identifier in record.stores


def discount_persistence_values(
    record: DiscountRecord, *, applies_to_store: bool, synced_at: datetime,
) -> dict:
    """Prepara columnas comerciales sin escribir ni asignar IDs locales.

    La decisión de tienda es explícita del llamador; no se infiere scope de ella.
    created_at pertenece a la inserción local, no al timestamp del proveedor.
    """
    if not isinstance(applies_to_store, bool):
        raise DiscountValidationError("La pertenencia a tienda debe ser explícita.")
    if not isinstance(synced_at, datetime) or synced_at.utcoffset() is None:
        raise DiscountValidationError("La fecha de sincronización requiere zona horaria.")

    return {
        "loyverse_id": record.loyverse_id,
        "name": record.name,
        "discount_type": record.discount_type.value,
        "value": record.value,
        "scope": "unknown",
        "is_active": applies_to_store and not record.is_deleted,
        "synced_at": synced_at.astimezone(timezone.utc),
    }
