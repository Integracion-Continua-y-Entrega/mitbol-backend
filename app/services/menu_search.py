"""HU-002: condición de búsqueda por nombre usada por el menú público."""

from sqlalchemy import Select

from app.models import Product


def apply_name_search(
    statement: Select[tuple[Product]],
    q: str | None,
) -> Select[tuple[Product]]:
    """Añade una condición por nombre sin ejecutar ni reemplazar la consulta.

    Decisiones propuestas para este ejercicio:
    - None, texto vacío o espacios exteriores: sin filtro si no queda texto.
    - Coincidencia parcial sin distinguir mayúsculas/minúsculas.
    - % y _ se buscan como caracteres literales, no como comodines.
    - Se conservan las condiciones previas, el orden y la carga de relaciones.
    """
    term = q.strip() if q is not None else ""
    if not term:
        return statement

    stmt = statement.where(
        Product.name.icontains(term, autoescape=True)
    )

    return stmt
