"""HU-010: contratos de solicitud y resultado de una operación individual."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enrichment import AdminInput


class ManualSyncRequest(AdminInput):
    """Solo selecciona el proceso; identidad, tienda y secretos son del servidor."""

    target: Literal["catalog", "discounts"]


Count = Annotated[int, Field(ge=0, strict=True)]


class RetiredCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    products: Count
    variants: Count
    modifiers: Count
    complements: Count


class CatalogSyncCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    products_processed: Count
    variants_processed: Count
    modifiers_processed: Count
    complements_processed: Count
    assignments_inserted: Count
    assignments_removed: Count
    retired: RetiredCounts


class DiscountSyncCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    discounts_processed: Count


class ManualSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: Literal["catalog", "discounts"]
    status: Literal["completed"]
    result: CatalogSyncCounts | DiscountSyncCounts

    @model_validator(mode="after")
    def matching_result(self):
        expected = CatalogSyncCounts if self.target == "catalog" else DiscountSyncCounts
        if not isinstance(self.result, expected):
            raise ValueError("El resultado no corresponde al proceso solicitado.")
        return self


def completed_response(target: Literal["catalog", "discounts"], raw: dict | int) -> ManualSyncResponse:
    counts = CatalogSyncCounts.model_validate(raw) if target == "catalog" else DiscountSyncCounts(discounts_processed=raw)
    return ManualSyncResponse(target=target, status="completed", result=counts)
