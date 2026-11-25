"""Cupones propios y origen de fidelización, sin alterar su ciclo de vida."""

from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, Discount, LoyaltyReward
from app.schemas.wallet import WalletCouponRead, WalletLoyaltyReward, WalletRead
from app.services import coupon_service


def _display_status(coupon, now):
    """Un canje es histórico; la expiración prevalece sobre reservas y disponibilidad."""
    if coupon.status == "redeemed":
        return "redeemed"
    if coupon.status == "expired" or now >= coupon.expires_at:
        return "expired"
    if coupon.status == "reserved":
        return "reserved"
    return "scheduled" if now < coupon.starts_at else "available"


def get_wallet(db: Session, *, account_id: int, now: datetime, limit=25, offset=0) -> WalletRead:
    """Pagina cupones y concesiones con la misma lectura, filtrando ambas entidades por cuenta.

    El llamador obtiene account_id de la sesión. El origen usa evidencia durable,
    no la configuración actual de metas. available conserva el significado
    temporal de CouponRead: la selección sigue revalidando condiciones e importes.
    """
    if (type(account_id) is not int or account_id <= 0 or type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or offset < 0):
        raise AuthError(422, "Parámetros de billetera inválidos.", code="WALLET_INPUT")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise AuthError(503, "El reloj de la billetera requiere zona horaria.", code="WALLET_CLOCK")
    if not db.scalar(text("""SELECT to_regclass('public."CustomerCoupons"') IS NOT NULL
        AND to_regclass('public."LoyaltyRewards"') IS NOT NULL""")):
        raise AuthError(503, "La billetera requiere la migración 0019.", code="WALLET_UNAVAILABLE")
    query = select(CustomerCoupon, Discount, LoyaltyReward).outerjoin(
        Discount, CustomerCoupon.discount_id == Discount.discount_id).outerjoin(LoyaltyReward, and_(
            LoyaltyReward.customer_coupon_id == CustomerCoupon.customer_coupon_id,
            LoyaltyReward.account_id == account_id)).where(CustomerCoupon.account_id == account_id).order_by(
                CustomerCoupon.customer_coupon_id.desc()).limit(limit + 1).offset(offset)
    rows = db.execute(query.execution_options(populate_existing=True)).all()
    coupons = []
    try:
        for coupon, discount, reward in rows[:limit]:
            info = coupon_service._read(coupon, discount, now)
            coupons.append(WalletCouponRead(**info.model_dump(), display_status=_display_status(coupon, now),
                source="loyalty_reward" if reward else "coupon",
                loyalty_reward=WalletLoyaltyReward.model_validate(reward) if reward else None))
    except ValidationError:
        raise AuthError(503, "Los beneficios almacenados requieren revisión.", code="WALLET_HISTORY") from None
    return WalletRead(coupons=coupons, limit=limit, offset=offset, has_more=len(rows) > limit)
