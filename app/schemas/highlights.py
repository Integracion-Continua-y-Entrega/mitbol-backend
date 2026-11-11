"""HU-006: contratos administrativos de destacados y novedades."""

from pydantic import BaseModel

from app.schemas.enrichment import AdminInput


class ProductHighlightsUpdate(AdminInput):
    """Configuración completa: ambos indicadores deben enviarse explícitamente."""
    is_featured: bool
    is_new: bool



class ProductHighlightsResponse(BaseModel):
    """Identificador del producto y sus dos indicadores almacenados."""

    product_id: int
    is_featured: bool
    is_new: bool
