"""Contrato público del menú; los importes siguen siendo Decimal en Python."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema


def _serialize_amount(value: Decimal) -> str:
    """Sin exponentes ni redondeo, con al menos dos decimales."""
    if value.is_zero():
        return "0.00"
    whole, _, fraction = format(value, "f").partition(".")
    return f"{whole}.{fraction.rstrip('0').ljust(2, '0')}"


MenuAmount = Annotated[
    Decimal,
    Field(strict=True, ge=0, allow_inf_nan=False),
    PlainSerializer(_serialize_amount, return_type=str, when_used="json"),
    WithJsonSchema(
        {"type": "string", "pattern": r"^[0-9]+\.[0-9]{2,}$",
         "examples": ["45.00", "90.1234567890123456789"]},
        mode="serialization",
    ),
]


class MenuVariant(BaseModel):
    product_variant_id: int = Field(description="ID interno estable de la variante.")
    name: str = Field(description="Nombre de la presentación registrado en PostgreSQL.")
    price: MenuAmount = Field(description="Importe exacto como cadena decimal, sin símbolo monetario.")
    is_available: bool = Field(
        description="Estado almacenado. No distingue agotamiento local de baja comercial."
    )
    can_order: bool = Field(
        description="Requiere variante disponible y producto padre activo y disponible."
    )


class MenuProduct(BaseModel):
    product_id: int = Field(description="ID interno estable del producto.")
    name: str = Field(description="Nombre del producto registrado en PostgreSQL.")
    description: str | None = Field(description="Descripción local; null cuando no existe.")
    image_url: str | None = Field(
        default=None, description="Reservado para fotografías; actualmente siempre null."
    )
    is_active: bool = Field(description="Activación comercial. El menú sólo incluye productos activos.")
    is_available: bool = Field(description="Disponibilidad local almacenada del producto.")
    can_order: bool = Field(
        description="Producto activo y disponible con al menos una variante disponible."
    )
    price_min: MenuAmount | None = Field(
        description="Menor precio de variantes disponibles; si no hay, referencia de todas. "
        "Null si no existen variantes."
    )
    price_max: MenuAmount | None = Field(
        description="Mayor precio del mismo conjunto utilizado para price_min."
    )
    has_price_range: bool = Field(
        description="True cuando price_min y price_max difieren; permite mostrar «Desde»."
    )
    variants: list[MenuVariant] = Field(
        description="Variantes ordenadas por ID, incluidas las no disponibles."
    )


class MenuResponse(BaseModel):
    currency: Literal["MXN"] = Field(
        default="MXN", description="Moneda acordada para MITBOL; no se realizan conversiones."
    )
    products: list[MenuProduct] = Field(
        description="Productos comercialmente activos, ordenados por ID; [] si el catálogo está vacío."
    )

    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "currency": "MXN",
                "products": [{
                    "product_id": 2,
                    "name": "Limonada natural",
                    "description": None,
                    "image_url": None,
                    "is_active": True,
                    "is_available": True,
                    "can_order": True,
                    "price_min": "35.00",
                    "price_max": "49.00",
                    "has_price_range": True,
                    "variants": [
                        {"product_variant_id": 2, "name": "355 ml", "price": "35.00",
                         "is_available": True, "can_order": True},
                        {"product_variant_id": 3, "name": "500 ml", "price": "49.00",
                         "is_available": True, "can_order": True},
                    ],
                }],
            },
            {"currency": "MXN", "products": []},
        ],
    })


class MenuErrorResponse(BaseModel):
    detail: str = Field(description="Mensaje público del error, sin detalles de infraestructura.")


class MenuComplement(BaseModel):
    product_complement_id: int = Field(description="ID interno estable del complemento.")
    name: str = Field(description="Nombre de la opción registrado en PostgreSQL.")
    price: MenuAmount = Field(
        description="Importe adicional exacto como cadena decimal; no es un total del pedido."
    )
    display_order: int = Field(description="Orden almacenado; se desempata por ID interno.")
    is_active: bool = Field(
        description="Activación comercial almacenada. Las opciones inactivas permanecen visibles."
    )
    can_order: bool = Field(
        description="Requiere complemento activo y can_order=true en la variante que lo contiene."
    )


class MenuModifier(BaseModel):
    product_modifier_id: int = Field(description="ID interno estable del grupo de modificadores.")
    name: str = Field(description="Nombre del grupo registrado en PostgreSQL.")
    display_order: int = Field(description="Orden almacenado; se desempata por ID interno.")
    complements: list[MenuComplement] = Field(
        description="Opciones ordenadas por display_order e ID, incluidas las inactivas. "
        "[] si el grupo no tiene opciones."
    )


class MenuVariantDetail(MenuVariant):
    modifiers: list[MenuModifier] = Field(
        description="Sólo los grupos asignados a esta variante, ordenados por display_order e ID. "
        "[] si no hay asignaciones. No se presuponen reglas de selección."
    )


class MenuProductDetail(MenuProduct):
    variants: list[MenuVariantDetail] = Field(
        description="Variantes ordenadas por ID, incluidas las no disponibles, con sus propios grupos."
    )


class MenuProductDetailResponse(BaseModel):
    currency: Literal["MXN"] = Field(
        default="MXN", description="Moneda acordada para MITBOL; no se realizan conversiones."
    )
    product: MenuProductDetail = Field(description="Detalle de un producto comercialmente activo.")

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "currency": "MXN",
            "product": {
                "product_id": 42,
                "name": "Hamburguesa clásica",
                "description": None,
                "image_url": None,
                "is_active": True,
                "is_available": True,
                "can_order": True,
                "price_min": "89.00",
                "price_max": "89.00",
                "has_price_range": False,
                "variants": [{
                    "product_variant_id": 101,
                    "name": "Sencilla",
                    "price": "89.00",
                    "is_available": True,
                    "can_order": True,
                    "modifiers": [{
                        "product_modifier_id": 7,
                        "name": "Extras",
                        "display_order": 1,
                        "complements": [
                            {"product_complement_id": 20, "name": "Sin cebolla", "price": "0.00",
                             "display_order": 1, "is_active": True, "can_order": True},
                            {"product_complement_id": 21, "name": "Queso", "price": "15.00",
                             "display_order": 2, "is_active": True, "can_order": True},
                            {"product_complement_id": 22, "name": "Tocino", "price": "20.00",
                             "display_order": 3, "is_active": False, "can_order": False},
                        ],
                    }],
                }],
            },
        }],
    })
