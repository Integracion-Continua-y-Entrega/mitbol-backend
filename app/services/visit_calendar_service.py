"""Día natural del restaurante calculado con la misma base horaria que verifica el registro SQL."""

from datetime import date, datetime

from sqlalchemy import Date, cast, func, select, text
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.core.session_policy import utc_datetime
from app.core.visit_calendar import DEFAULT_RESTAURANT_TIMEZONE, valid_timezone_name


def visit_day(db: Session, registered_at: datetime, *, restaurant_timezone=DEFAULT_RESTAURANT_TIMEZONE) -> date:
    """No usa la zona del proceso ni offsets fijos; la configuración debe existir en PostgreSQL."""
    if (not isinstance(registered_at, datetime) or registered_at.utcoffset() is None
            or not valid_timezone_name(restaurant_timezone)):
        raise AuthError(503, "El calendario de visitas requiere fecha con zona horaria y una zona válida.", code="VISIT_CALENDAR_INVALID")
    exists = db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=:zone)"),
                       {"zone": restaurant_timezone})
    if not exists:
        raise AuthError(503, "La zona horaria del restaurante no está disponible.", code="VISIT_CALENDAR_INVALID")
    return db.scalar(select(cast(func.timezone(restaurant_timezone, utc_datetime(registered_at)), Date)))
