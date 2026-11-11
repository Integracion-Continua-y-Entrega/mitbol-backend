"""HU-002: filtro por etiquetas usado por el servicio público del menú."""

from sqlalchemy import Select, select

from app.models import Product, ProductTag, ProductVariant


def apply_tag_filter(
    statement: Select[tuple[Product]],
    tag_ids: list[int] | None,
) -> Select[tuple[Product]]:
    """Filtra productos asociados a cualquiera de las etiquetas indicadas.

    Contrato propuesto: OR entre etiquetas, AND con la búsqueda por nombre.
    None o [] conserva la consulta. No elimina variantes de la respuesta,
    no oculta agotados y no duplica productos por coincidencias múltiples.
    Los IDs positivos se validarán en el endpoint al conectar este auxiliar.
    """
    if not tag_ids:
        return statement

    subquery = (
        select(ProductVariant.product_id)
        .join(ProductTag, ProductTag.product_variant_id == ProductVariant.product_variant_id)
        .where(ProductTag.tag_id.in_(tag_ids))
    )

    statement = statement.where(Product.product_id.in_(subquery))

    return statement
