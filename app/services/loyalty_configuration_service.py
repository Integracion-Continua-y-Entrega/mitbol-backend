"""Configuración de HU-046; no registra visitas, concede recompensas ni edita cupones."""

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.models import Discount, LoyaltyGoal, Product
from app.schemas.loyalty import LoyaltyGoalInput, LoyaltyGoalList, LoyaltyGoalRead, LoyaltyGoalUpdate, LoyaltyGoalVersion
from app.services.commercial_discount_service import AMOUNT_TYPES, PERCENT_TYPES
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY


def _require_schema(db):
    if not db.scalar(text("""SELECT to_regclass('public."LoyaltyGoals"') IS NOT NULL""")):
        raise AuthError(503, "La configuración de fidelización requiere la migración 0016.", code="LOYALTY_UNAVAILABLE")


def _read(row):
    try:
        return LoyaltyGoalRead.model_validate(row)
    except ValidationError:
        raise AuthError(503, "La configuración almacenada requiere revisión.", code="LOYALTY_INVALID_HISTORY") from None


def _input(payload, model):
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise AuthError(422, "Configuración de fidelización inválida.", code="LOYALTY_INVALID_INPUT") from None


def _mutation_lock(db):
    """Comparte exclusión con sincronización y disponibilidad hasta el commit del llamador."""
    _require_schema(db)
    if not db.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
        raise AuthError(409, "Hay otra operación de catálogo en curso; consulta y reintenta.", code="CATALOG_BUSY")


def _goal(db, goal_id, *, lock=False):
    query = select(LoyaltyGoal).where(LoyaltyGoal.goal_id == goal_id).execution_options(populate_existing=True)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AuthError(404, "Meta no encontrada.")
    return row


def _references(db, payload):
    """Exige descuentos monetarios calculables y productos activos; no exige existencias futuras."""
    identifiers = {payload.discount_id, *(payload.conditions.compatible_discount_ids or [])}
    discounts = db.scalars(select(Discount).where(Discount.discount_id.in_(identifiers))
                           .execution_options(populate_existing=True)).all()
    if {row.discount_id for row in discounts} != identifiers:
        raise AuthError(404, "Un descuento referido no existe.", code="LOYALTY_REFERENCE_NOT_FOUND")
    for discount in discounts:
        value, kind = discount.value, discount.discount_type
        if (not discount.is_active or kind not in AMOUNT_TYPES | PERCENT_TYPES or value is None
                or not value.is_finite() or value <= 0 or (kind in PERCENT_TYPES and value > 100)):
            raise AuthError(409, "Un descuento referido no está disponible o no tiene un valor calculable.",
                            code="LOYALTY_INVALID_BENEFIT")
    products = set(payload.conditions.product_ids or [])
    if products:
        rows = db.execute(select(Product.product_id, Product.is_active).where(Product.product_id.in_(products))).all()
        if {row.product_id for row in rows} != products:
            raise AuthError(404, "Un producto referido no existe.", code="LOYALTY_REFERENCE_NOT_FOUND")
        if not all(row.is_active for row in rows):
            raise AuthError(409, "Los productos referidos deben estar activos.", code="LOYALTY_INVALID_PRODUCTS")


def _flush(db):
    try:
        db.flush()
    except IntegrityError as error:
        if (getattr(error.orig, "pgcode", None) == "23505"
                and getattr(getattr(error.orig, "diag", None), "constraint_name", None) == "uq_LoyaltyGoals_required_visits"):
            raise AuthError(409, "Ya existe una meta con ese número de visitas.", code="LOYALTY_THRESHOLD_EXISTS") from None
        raise


def _check_version(row, version):
    if row.version != version:
        raise AuthError(409, "La meta cambió; consulta su versión actual antes de editar.", code="LOYALTY_STALE_VERSION")


def list_goals(db: Session, *, is_active=None, limit=25, offset=0) -> LoyaltyGoalList:
    """Incluye metas inactivas para administración, ordenadas por visitas antes de paginar."""
    _require_schema(db)
    filters = [LoyaltyGoal.is_active == is_active] if is_active is not None else []
    total = db.scalar(select(func.count()).select_from(LoyaltyGoal).where(*filters))
    rows = db.scalars(select(LoyaltyGoal).where(*filters).order_by(LoyaltyGoal.required_visits, LoyaltyGoal.goal_id)
                      .limit(limit).offset(offset))
    return LoyaltyGoalList(total=total, limit=limit, offset=offset, items=[_read(row) for row in rows])


def get_goal(db: Session, goal_id) -> LoyaltyGoalRead:
    _require_schema(db)
    return _read(_goal(db, goal_id))


def create_goal(db: Session, payload: LoyaltyGoalInput, *, actor_account_id: int) -> LoyaltyGoalRead:
    """Verifica referencias bajo bloqueo; no copia precios monetarios ni crea beneficios para clientes."""
    payload = _input(payload, LoyaltyGoalInput)
    _mutation_lock(db)
    _references(db, payload)
    row = LoyaltyGoal(**payload.model_dump(mode="json"), updated_by_account_id=actor_account_id)
    db.add(row)
    _flush(db)
    return _read(row)


def update_goal(db: Session, goal_id, payload: LoyaltyGoalUpdate, *, actor_account_id: int) -> LoyaltyGoalRead:
    """Reemplaza la configuración revisada manteniendo su identidad; la versión evita ediciones perdidas."""
    payload = _input(payload, LoyaltyGoalUpdate)
    _mutation_lock(db)
    row = _goal(db, goal_id, lock=True)
    _check_version(row, payload.version)
    _references(db, payload)
    for name, value in payload.model_dump(mode="json", exclude={"version"}).items():
        setattr(row, name, value)
    row.version += 1
    row.updated_by_account_id = actor_account_id
    row.updated_at = db.scalar(select(func.clock_timestamp()))
    _flush(db)
    return _read(row)


def deactivate_goal(db: Session, goal_id, payload: LoyaltyGoalVersion, *, actor_account_id: int) -> LoyaltyGoalRead:
    """Permite retirar una meta aunque su referencia comercial deje de ser utilizable."""
    payload = _input(payload, LoyaltyGoalVersion)
    _mutation_lock(db)
    row = _goal(db, goal_id, lock=True)
    _check_version(row, payload.version)
    if row.is_active:
        row.is_active = False
        row.version += 1
        row.updated_by_account_id = actor_account_id
        row.updated_at = db.scalar(select(func.clock_timestamp()))
        _flush(db)
    return _read(row)
