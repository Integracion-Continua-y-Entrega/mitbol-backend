"""Registro presencial: la credencial y la visita se confirman o revierten juntas."""

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.core.loyalty_identification import hash_identification
from app.core.visit_calendar import DEFAULT_RESTAURANT_TIMEZONE
from app.models import LoyaltyIdentification, LoyaltyVisit
from app.schemas.loyalty_identification import LoyaltyIdentificationInput
from app.schemas.loyalty_visits import LoyaltyVisitRead
from app.services.loyalty_identification_service import consume_identification
from app.services.visit_calendar_service import visit_day


def register_visit(db: Session, payload: LoyaltyIdentificationInput, *, actor_account_id: int, clock,
                   restaurant_timezone=DEFAULT_RESTAURANT_TIMEZONE) -> LoyaltyVisitRead:
    """Usa la fecha del consumo validado, no una segunda lectura del reloj tras cruzar medianoche.

    El llamador debe compartir su transacción de autenticación y confirmar antes
    de responder. Un conflicto de unicidad revierte también el consumo del QR.
    """
    try:
        payload = LoyaltyIdentificationInput.model_validate(payload)
        digest = hash_identification(payload.qr_payload)
    except (ValidationError, ValueError):
        raise AuthError(422, "Identificación de fidelización inválida.", code="LOYALTY_IDENTIFICATION_INPUT") from None
    if not db.scalar(text("""SELECT to_regclass('public."LoyaltyVisits"') IS NOT NULL""")):
        raise AuthError(503, "El registro de visitas requiere la migración 0018.", code="VISITS_UNAVAILABLE")
    identified = consume_identification(db, payload, actor_account_id=actor_account_id, clock=clock)
    registered_at = db.scalar(select(LoyaltyIdentification.consumed_at).where(
        LoyaltyIdentification.account_id == identified.account_id, LoyaltyIdentification.token_hash == digest))
    if registered_at is None:
        raise AuthError(503, "No fue posible confirmar la identificación.", code="VISIT_CONSUMPTION_UNAVAILABLE")
    local_day = visit_day(db, registered_at, restaurant_timezone=restaurant_timezone)
    row = LoyaltyVisit(account_id=identified.account_id, visited_on=local_day, registered_at=registered_at,
        restaurant_timezone=restaurant_timezone, registered_by_account_id=actor_account_id, identification_hash=digest)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        if getattr(error.orig, "pgcode", None) == "23505":
            if constraint == "uq_LoyaltyVisits_account_day":
                raise AuthError(409, "La cuenta ya tiene una visita registrada para este día.", code="VISIT_ALREADY_REGISTERED") from None
            if constraint == "uq_LoyaltyVisits_identification_hash":
                raise AuthError(409, "La identificación ya se utilizó para una visita.", code="VISIT_IDENTIFICATION_REUSED") from None
        raise
    return LoyaltyVisitRead.model_validate(row)
