"""Lectura del catálogo de HU-008, sin escrituras ni llamadas a Loyverse."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session, selectinload

from app.models import Product, ProductImage, ProductModifier, ProductModifierAssignment, ProductTag, ProductVariant, Tag
from app.services.availability_rules import product_can_order, variant_can_order
from app.schemas.menu import (
    MenuComplement,
    MenuModifier,
    MenuProduct,
    MenuProductDetail,
    MenuProductDetailResponse,
    MenuResponse,
    MenuVariant,
    MenuVariantDetail,
    MenuImage,
    MenuTag,
)
from app.services.menu_search import apply_name_search
from app.services.menu_tag_filter import apply_tag_filter


class MenuReadError(RuntimeError):
    """La consulta o los datos del catálogo no permiten construir la respuesta."""


class MenuUnavailableError(MenuReadError):
    """La conexión a la base de datos no está disponible temporalmente."""


class MenuProductNotFoundError(LookupError):
    """El producto no existe o no debe publicarse por estar comercialmente inactivo."""


def _load_enrichment(db: Session, products: list[Product]) -> tuple[dict, dict]:
    identifiers = [variant.product_variant_id for product in products for variant in product.variants]
    images, tags = {}, {}
    # Lotes acotados: evita parámetros ilimitados y mantiene las consultas independientes
    # del número de variantes dentro de cada lote, igual que selectinload.
    for offset in range(0, len(identifiers), 500):
        batch = identifiers[offset:offset + 500]
        for image in db.scalars(select(ProductImage).where(
            ProductImage.product_variant_id.in_(batch),
        ).order_by(ProductImage.display_order, ProductImage.product_image_id)):
            images.setdefault(image.product_variant_id, []).append(image)
        for variant_id, tag in db.execute(select(ProductTag.product_variant_id, Tag).join(
            Tag, Tag.tag_id == ProductTag.tag_id,
        ).where(ProductTag.product_variant_id.in_(batch)).order_by(Tag.tag_id)):
            tags.setdefault(variant_id, []).append(MenuTag.model_validate(tag))
    return images, tags


def _build_product(product: Product, images: dict, tags: dict) -> MenuProduct:
    variants = [
        MenuVariant(
            product_variant_id=variant.product_variant_id,
            name=variant.name,
            price=variant.price,
            is_available=variant.is_available,
            can_order=variant_can_order(product, variant),
            image_url=(images[variant.product_variant_id][0].image_url
                       if images.get(variant.product_variant_id) else None),
            tags=tags.get(variant.product_variant_id, []),
        )
        for variant in sorted(product.variants, key=lambda value: value.product_variant_id)
    ]
    available_variants = [variant for variant in variants if variant.can_order]
    # Si todas están agotadas, los importes se conservan únicamente como referencia.
    prices = [variant.price for variant in (available_variants or variants)]
    price_min = min(prices, default=None)
    price_max = max(prices, default=None)

    representative = min(
        (image for variant in product.variants for image in images.get(variant.product_variant_id, [])),
        key=lambda image: (image.display_order, image.product_variant_id, image.product_image_id),
        default=None,
    )
    return MenuProduct(
        product_id=product.product_id,
        name=product.name,
        description=product.description,
        image_url=representative.image_url if representative else None,
        is_active=product.is_active,
        is_available=product.is_available,
        can_order=product_can_order(product),
        price_min=price_min,
        price_max=price_max,
        has_price_range=price_min != price_max,
        variants=variants,
    )


def get_menu(db: Session, q: str | None = None, tag_ids: list[int] | None = None) -> MenuResponse:
    """Carga productos y variantes por lotes; la sesión pertenece a get_db."""
    statement = (
        select(Product)
        .where(Product.is_active.is_(True))
        .options(selectinload(Product.variants))
        .order_by(Product.product_id)
    )
    statement = apply_name_search(statement=statement, q=q)
    statement = apply_tag_filter(statement=statement, tag_ids=tag_ids)
    try:
        products = db.scalars(statement).all()
        images, tags = _load_enrichment(db, products)
        return MenuResponse(products=[_build_product(product, images, tags) for product in products])
    except (OperationalError, InterfaceError, TimeoutError) as error:
        raise MenuUnavailableError("El menú no está disponible temporalmente.") from error
    except (SQLAlchemyError, ValidationError) as error:
        raise MenuReadError("No fue posible consultar el menú.") from error


def _build_modifier(modifier: ProductModifier, *, variant_can_order: bool) -> MenuModifier:
    return MenuModifier(
        product_modifier_id=modifier.product_modifier_id,
        name=modifier.name,
        display_order=modifier.display_order,
        complements=[
            MenuComplement(
                product_complement_id=complement.product_complement_id,
                name=complement.name,
                price=complement.price,
                display_order=complement.display_order,
                is_active=complement.is_active,
                can_order=variant_can_order and complement.is_active,
            )
            for complement in sorted(
                modifier.complements,
                key=lambda value: (value.display_order, value.product_complement_id),
            )
        ],
    )


def _build_product_detail(product: Product, images: dict, tags: dict) -> MenuProductDetail:
    # HU-001 sigue siendo la única fuente de reglas para precios y disponibilidad.
    base_product = _build_product(product, images, tags)
    variants_by_id = {variant.product_variant_id: variant for variant in product.variants}
    variants = []
    for base_variant in base_product.variants:
        variant = variants_by_id[base_variant.product_variant_id]
        modifiers = [
            _build_modifier(assignment.modifier, variant_can_order=base_variant.can_order)
            for assignment in sorted(
                variant.modifier_assignments,
                key=lambda value: (value.modifier.display_order, value.product_modifier_id),
            )
        ]
        variants.append(MenuVariantDetail(
            **base_variant.model_dump(mode="python"), modifiers=modifiers,
            images=[MenuImage.model_validate(image) for image in images.get(variant.product_variant_id, [])],
        ))
    # El modo Python conserva Decimal hasta la serialización final de la respuesta.
    return MenuProductDetail(
        **base_product.model_dump(mode="python", exclude={"variants"}), variants=variants,
    )


def get_product_detail(db: Session, product_id: int) -> MenuProductDetailResponse:
    """Lee el árbol por lotes; sólo recorre las asignaciones de las variantes del producto."""
    statement = (
        select(Product)
        .where(Product.product_id == product_id, Product.is_active.is_(True))
        .options(
            selectinload(Product.variants)
            .selectinload(ProductVariant.modifier_assignments)
            .joinedload(ProductModifierAssignment.modifier)
            .selectinload(ProductModifier.complements)
        )
    )
    try:
        product = db.scalars(statement).one_or_none()
        if product is None:
            raise MenuProductNotFoundError("Producto no encontrado.")
        images, tags = _load_enrichment(db, [product])
        return MenuProductDetailResponse(product=_build_product_detail(product, images, tags))
    except (OperationalError, InterfaceError, TimeoutError) as error:
        raise MenuUnavailableError("El detalle del producto no está disponible temporalmente.") from error
    except (SQLAlchemyError, ValidationError) as error:
        raise MenuReadError("No fue posible consultar el detalle del producto.") from error
