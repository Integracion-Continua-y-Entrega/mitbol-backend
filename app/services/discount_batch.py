"""Preparación de un lote validado para una tienda, sin ejecutar SQL."""

from app.services.discount_probe import DiscountSnapshot
from app.services.discount_transform import (
    discount_applies_to_store,
    discount_persistence_values,
)


def prepare_discount_batch(snapshot: DiscountSnapshot, store_id: str) -> tuple[dict, ...]:
    """Prepara todas las filas con una única fecha de sincronización.

    No filtra registros de otras tiendas: se conservan como inactivos.
    No determina bajas por ausencia ni abre sesiones o transacciones.
    """
    return tuple(
        discount_persistence_values(
            record,
            applies_to_store=discount_applies_to_store(record, store_id),
            synced_at=snapshot.fetched_at,
        )
        for record in snapshot.records
    )
