"""HU-002: lectura de etiquetas para el endpoint público del menú."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session

from app.models import Tag
from app.schemas.menu import MenuTag
from app.services.menu_service import MenuReadError, MenuUnavailableError


def get_menu_tags(db: Session) -> list[MenuTag]:
    """Lee todas las etiquetas registradas, ordenadas por nombre e ID.

    Propuesta para este primer listado: incluye etiquetas sin asignaciones.
    No calcula conteos ni filtra productos. Una tabla vacía devuelve [].
    La sesión pertenece al llamador: aquí no se abre ni se cierra.
    """
    try:
        statement = select(Tag).order_by(Tag.name, Tag.tag_id)
        tags = db.scalars(statement).all()
        return [MenuTag.model_validate(tag) for tag in tags]
    except (OperationalError, InterfaceError, TimeoutError) as error:
        raise MenuUnavailableError("Las etiquetas no están disponibles temporalmente.") from error
    except (SQLAlchemyError, ValidationError) as error:
        raise MenuReadError("No fue posible consultar las etiquetas.") from error
