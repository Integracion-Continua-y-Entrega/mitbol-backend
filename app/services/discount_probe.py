"""Lectura y validación completa de descuentos; sin persistencia ni selección comercial."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.catalog_probe import select_store
from app.services.discount_rules import DiscountType
from app.services.discount_transform import (
    DiscountRecord, DiscountValidationError, discount_identity, discount_metadata,
    parse_discount, parse_discount_records,
)
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError, LoyverseResponseError


@dataclass(frozen=True)
class DiscountSnapshot:
    records: tuple[DiscountRecord, ...]
    includes_deleted: bool
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quarantined: tuple[dict, ...] = ()
    resolutions: tuple[dict, ...] = ()
    source_count: int | None = None
    verified_store_id: str | None = None


def _comparable(record: dict) -> dict:
    return {**record, "id": discount_identity(record)}


async def _resolve(client, identifier, versions):
    current = None
    try:
        current = await client.get_discount(identifier)
        discount_metadata(current)
        if discount_identity(current) != identifier:
            return None, "DISCOUNT_DETAIL_IDENTITY_MISMATCH"
        if current["type"] in DiscountType._value2member_map_:
            parse_discount(current)
        if not any(_comparable(current) == _comparable(row) for row in versions):
            return current, "DISCOUNT_DETAIL_NOT_OBSERVED"
        return current, None
    except (DiscountValidationError, LoyverseResponseError):
        return current, "DISCOUNT_DETAIL_INVALID"
    except LoyverseAPIError as error:
        if error.status_code == 404:
            return None, "DISCOUNT_DETAIL_NOT_FOUND"
        # Autenticación, suscripción, transporte y errores temporales invalidan la ejecución.
        raise


async def fetch_discount_snapshot(
    client: LoyverseAPIClient, *, include_deleted: bool = True,
    store_id: str | None = None, collect_issues: bool = False,
) -> DiscountSnapshot:
    verified_store = None
    if store_id is not None:
        verified_store = select_store(await client.get_stores(), store_id)["id"]
    records = await client.get_discounts(include_deleted=include_deleted)
    if not isinstance(records, list):
        raise DiscountValidationError("Se esperaba una lista de descuentos.")
    groups = defaultdict(list)
    # La identidad y estructura de TODA la colección deben ser válidas antes de aislar IDs.
    for record in records:
        metadata = discount_metadata(record)
        groups[metadata["loyverse_id"]].append(deepcopy(record))
    accepted, quarantined, resolutions = [], [], []
    for identifier, versions in groups.items():
        chosen = versions[0]
        if len(versions) > 1:
            identical = all(_comparable(row) == _comparable(chosen) for row in versions[1:])
            reason = None
            if not identical:
                chosen, reason = await _resolve(client, identifier, versions)
            if reason:
                quarantined.append(dict(loyverse_id=identifier, reason=reason,
                    originals=versions, individual=chosen))
                continue
            discarded = deepcopy(versions)
            selected_index = next(i for i, row in enumerate(discarded)
                                  if _comparable(row) == _comparable(chosen))
            discarded.pop(selected_index)
            resolutions.append(dict(loyverse_id=identifier,
                reason="DISCOUNT_IDENTICAL_COPIES" if identical else "DISCOUNT_CURRENT_CONFIRMED",
                originals=versions, selected=deepcopy(chosen), discarded=discarded))
        if chosen["type"] not in DiscountType._value2member_map_:
            quarantined.append(dict(loyverse_id=identifier, reason="DISCOUNT_UNSUPPORTED_TYPE",
                originals=versions, individual=deepcopy(chosen)))
        else:
            accepted.append(chosen)
    parsed = parse_discount_records(accepted)
    if quarantined and not collect_issues:
        raise DiscountValidationError("Hay descuentos aislados; el modo estricto rechaza el lote.")
    return DiscountSnapshot(parsed, include_deleted, quarantined=tuple(quarantined),
        resolutions=tuple(resolutions), source_count=len(records), verified_store_id=verified_store)
