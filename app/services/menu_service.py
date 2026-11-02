"""Lectura del catálogo de HU-008, sin escrituras ni llamadas a Loyverse."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session, selectinload

from app.models import Product
from app.schemas.menu import MenuProduct, MenuResponse, MenuVariant


class MenuReadError(RuntimeError):
    """La consulta o los datos del catálogo no permiten construir la respuesta."""


class MenuUnavailableError(MenuReadError):
    """La conexión a la base de datos no está disponible temporalmente."""


def _build_product(product: Product) -> MenuProduct:
    parent_available = product.is_active and product.is_available
    variants = [
        MenuVariant(
            product_variant_id=variant.product_variant_id,
            name=variant.name,
            price=variant.price,
            is_available=variant.is_available,
            can_order=parent_available and variant.is_available,
        )
        for variant in sorted(product.variants, key=lambda value: value.product_variant_id)
    ]
    available_variants = [variant for variant in variants if variant.is_available]
    # Si todas están agotadas, los importes se conservan únicamente como referencia.
    prices = [variant.price for variant in (available_variants or variants)]
    price_min = min(prices, default=None)
    price_max = max(prices, default=None)

    return MenuProduct(
        product_id=product.product_id,
        name=product.name,
        description=product.description,
        image_url=None,
        is_active=product.is_active,
        is_available=product.is_available,
        can_order=parent_available and bool(available_variants),
        price_min=price_min,
        price_max=price_max,
        has_price_range=price_min != price_max,
        variants=variants,
    )


def get_menu(db: Session) -> MenuResponse:
    """Carga productos y variantes por lotes; la sesión pertenece a get_db."""
    statement = (
        select(Product)
        .where(Product.is_active.is_(True))
        .options(selectinload(Product.variants))
        .order_by(Product.product_id)
    )
    try:
        products = db.scalars(statement).all()
        return MenuResponse(products=[_build_product(product) for product in products])
    except (OperationalError, InterfaceError, TimeoutError) as error:
        raise MenuUnavailableError("El menú no está disponible temporalmente.") from error
    except (SQLAlchemyError, ValidationError) as error:
        raise MenuReadError("No fue posible consultar el menú.") from error
