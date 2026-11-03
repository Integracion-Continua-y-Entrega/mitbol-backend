"""Contratos administrativos: solamente is_available admite escrituras."""

from pydantic import BaseModel

from app.schemas.enrichment import AdminInput


class AvailabilityUpdate(AdminInput):
    is_available: bool


class ProductAvailabilityResponse(BaseModel):
    product_id: int
    is_active: bool
    is_available: bool
    can_order: bool


class VariantAvailabilityResponse(BaseModel):
    product_variant_id: int
    product_id: int
    is_available: bool
    is_commercially_available: bool
    product_is_active: bool
    product_is_available: bool
    can_order: bool
