"""Lectura y validación completa de descuentos; sin persistencia ni selección comercial."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.discount_transform import DiscountRecord, parse_discount_records
from app.services.loyverse_client import LoyverseAPIClient


@dataclass(frozen=True)
class DiscountSnapshot:
    records: tuple[DiscountRecord, ...]
    includes_deleted: bool
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def fetch_discount_snapshot(
    client: LoyverseAPIClient, *, include_deleted: bool = True,
) -> DiscountSnapshot:
    records = await client.get_discounts(include_deleted=include_deleted)
    # No entregar un snapshot parcialmente validado ni inferir bajas por ausencias.
    return DiscountSnapshot(parse_discount_records(records), include_deleted)
