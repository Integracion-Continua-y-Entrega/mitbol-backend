"""Contrato público con PostgreSQL aislado, sin escribir en mitbol_dev."""

from app.models import catalog_metadata
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError, TimeoutError
from sqlalchemy.orm import sessionmaker

from app.db.database import get_db
from app.models import (
    Base, Product, ProductComplement, ProductModifier, ProductModifierAssignment, ProductTag, ProductVariant, Tag,
)
from app.schemas.menu import MenuComplement, MenuProductDetailResponse, MenuResponse, MenuVariant


@pytest.fixture(params=["postgresql"])
def menu_db(request):
    engine = request.getfixturevalue("sync_test_database")
    assert engine.url.database.startswith("mitbol_test_hu008_")
    with engine.connect() as connection:
        transaction = connection.begin()
        sessions = sessionmaker(
            bind=connection, autoflush=False, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield SimpleNamespace(connection=connection, sessions=sessions)
        finally:
            transaction.rollback()


@pytest.fixture
def add_product(menu_db):
    def add(*, prices=("45.00",), available=True, active=True, flags=None,
            name="Producto de prueba", description=None, featured=False, new=True):
        timestamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
        product = Product(
            loyverse_id="hu001-product-" + uuid4().hex, name=name,
            description=description, is_active=active, is_available=available,
            is_featured=featured, is_new=new,
            synced_at=timestamp,
        )
        product.variants = [
            ProductVariant(
                loyverse_id="hu001-variant-" + uuid4().hex,
                name=f"Presentación {index + 1}", price=Decimal(price),
                is_available=flags[index] if flags is not None else True,
                is_commercially_available=True,
                synced_at=timestamp,
            )
            for index, price in enumerate(prices)
        ]
        with menu_db.sessions() as session, session.begin():
            session.add(product)
            session.flush()
        return product
    return add


@pytest.fixture
def tagged_menu(menu_db, add_product):
    """HU-002: escenario sintético para practicar pruebas de filtros reales."""
    burger = add_product(name="Hamburguesa Doble", prices=("89.00", "129.00"), available=False)
    lemonade = add_product(name="Limonada")
    hidden = add_product(name="Hamburguesa oculta", active=False)
    plain = add_product(name="Papas sin etiquetas")
    suffix = uuid4().hex
    with menu_db.sessions() as session, session.begin():
        first = Tag(name=f"hu002-primera-{suffix}")
        second = Tag(name=f"hu002-segunda-{suffix}")
        unused = Tag(name=f"hu002-sin-asignar-{suffix}")
        session.add_all([first, second, unused])
        session.flush()
        first_id, second_id, unused_id = first.tag_id, second.tag_id, unused.tag_id
        session.add_all([
            ProductTag(product_variant_id=variant.product_variant_id, tag_id=first_id)
            for variant in burger.variants
        ])
        session.add_all([
            ProductTag(product_variant_id=lemonade.variants[0].product_variant_id, tag_id=second_id),
            ProductTag(product_variant_id=hidden.variants[0].product_variant_id, tag_id=first_id),
        ])
    return SimpleNamespace(
        burger=burger, lemonade=lemonade, hidden=hidden, plain=plain,
        first_id=first_id, second_id=second_id, unused_id=unused_id,
    )


def test_menu_tag_filter_keeps_whole_product(menu_client, tagged_menu):
    """Dos variantes coincidentes producen un único producto, incluso agotado."""
    response = menu_client.get("/api/menu", params={"tag_id": tagged_menu.first_id})

    assert response.status_code == 200
    products = response.json()["products"]
    assert len(products) == 1
    product = products[0]
    assert product["product_id"] == tagged_menu.burger.product_id
    assert product["is_available"] is False
    assert product["can_order"] is False
    assert [variant["product_variant_id"] for variant in product["variants"]] == [
        variant.product_variant_id for variant in tagged_menu.burger.variants
    ]
    assert (product["price_min"], product["price_max"]) == ("89.00", "129.00")


def test_menu_tag_filter_keeps_nonmatching_variants(menu_client, menu_db, tagged_menu):
    """Coincidir solo en una variante no recorta variantes ni altera precios."""
    unmatched = tagged_menu.burger.variants[1]
    with menu_db.sessions() as session, session.begin():
        session.execute(delete(ProductTag).where(
            ProductTag.product_variant_id == unmatched.product_variant_id,
        ))

    response = menu_client.get("/api/menu", params={"tag_id": tagged_menu.first_id})

    assert response.status_code == 200
    product, = response.json()["products"]
    assert product["product_id"] == tagged_menu.burger.product_id
    assert [variant["product_variant_id"] for variant in product["variants"]] == [
        variant.product_variant_id for variant in tagged_menu.burger.variants
    ]
    assert product["variants"][0]["tags"]
    assert product["variants"][1]["tags"] == []
    assert (product["price_min"], product["price_max"]) == ("89.00", "129.00")


@pytest.mark.parametrize("tag_keys,q,expected_keys", [
    ([], None, ["burger", "lemonade", "plain"]),
    (["first_id"], None, ["burger"]),
    (["second_id"], None, ["lemonade"]),
    (["first_id", "second_id"], None, ["burger", "lemonade"]),
    (["first_id", "first_id"], None, ["burger"]),
    (["unused_id"], None, []),
    (["missing_id"], None, []),
    (["missing_id", "second_id"], None, ["lemonade"]),
    (["first_id"], "  DOBLE  ", ["burger"]),
    (["second_id"], "doble", []),
    (["first_id", "second_id"], "  LIM  ", ["lemonade"]),
    (["first_id", "second_id"], "   ", ["burger", "lemonade"]),
])
def test_menu_tag_filter_combinations(menu_client, tagged_menu, tag_keys, q, expected_keys):
    tagged_menu.missing_id = max(tagged_menu.first_id, tagged_menu.second_id, tagged_menu.unused_id) + 1_000_000
    params = [("tag_id", getattr(tagged_menu, key)) for key in tag_keys]
    if q is not None:
        params.append(("q", q))

    response = menu_client.get("/api/menu", params=params)

    assert response.status_code == 200
    identifiers = [product["product_id"] for product in response.json()["products"]]
    expected = [getattr(tagged_menu, key).product_id for key in expected_keys]
    assert identifiers == expected
    assert identifiers == sorted(set(identifiers))


def test_public_tags_postgresql_lists_unassigned_and_sorts_by_name(menu_client, menu_db, tagged_menu):
    # Insertar esta etiqueta al final distingue orden alfabético de orden por ID.
    with menu_db.sessions() as session, session.begin():
        first_by_name = Tag(name=f"a-hu002-{uuid4().hex}")
        session.add(first_by_name)
        session.flush()
        first_id = first_by_name.tag_id

    response = menu_client.get("/api/menu/tags")

    assert response.status_code == 200
    tags = response.json()
    assert [tag["tag_id"] for tag in tags] == [
        first_id, tagged_menu.first_id, tagged_menu.second_id, tagged_menu.unused_id,
    ]
    assert [tag["name"] for tag in tags] == sorted(tag["name"] for tag in tags)
    assert all(set(tag) == {"tag_id", "name"} for tag in tags)


def test_public_tags_postgresql_empty_returns_200(menu_client):
    response = menu_client.get("/api/menu/tags")
    assert response.status_code == 200
    assert response.json() == []


@pytest.fixture
def api_app():
    # Resolver la aplicación aquí también funciona después del reload de test_migrations.
    from app.main import app

    previous_overrides = app.dependency_overrides.copy()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


@pytest.fixture
def menu_client(api_app, menu_db):
    def isolated_db():
        with menu_db.sessions() as session:
            yield session

    api_app.dependency_overrides[get_db] = isolated_db
    with TestClient(api_app) as client:
        yield client


@contextmanager
def capture_sql(connection):
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", record)


def test_menu_is_public_and_preserves_contract(menu_client, add_product):
    product = add_product(name="Hamburguesa clásica", description="Descripción local MITBOL")
    response = menu_client.get("/api/menu")
    assert "authorization" not in response.request.headers
    assert "cookie" not in response.request.headers
    assert response.status_code == 200
    assert response.json() == {
        "currency": "MXN",
        "products": [{
            "product_id": product.product_id,
            "name": "Hamburguesa clásica",
            "description": "Descripción local MITBOL",
            "image_url": None,
            "is_active": True,
            "is_featured": False,
            "is_new": True,
            "is_available": True,
            "can_order": True,
            "price_min": "45.00",
            "price_max": "45.00",
            "has_price_range": False,
            "variants": [{
                "product_variant_id": product.variants[0].product_variant_id,
                "name": "Presentación 1",
                "price": "45.00",
                "is_available": True,
                "can_order": True,
                "image_url": None,
                "tags": [],
            }],
        }],
    }


@pytest.mark.parametrize("prices,minimum,maximum,has_range", [
    (("45.00",), "45.00", "45.00", False),
    (("49.00", "35.00"), "35.00", "49.00", True),
    (("89.00", "129.00"), "89.00", "129.00", True),
    (("10.00", "10.00"), "10.00", "10.00", False),
    (("0.00", "5.00"), "0.00", "5.00", True),
])
def test_single_and_multiple_variant_prices(menu_client, add_product, prices, minimum, maximum, has_range):
    add_product(prices=prices)
    response = menu_client.get("/api/menu")
    assert response.status_code == 200
    product = response.json()["products"][0]
    assert product["price_min"] == minimum
    assert product["price_max"] == maximum
    assert product["has_price_range"] is has_range
    assert [variant["price"] for variant in product["variants"]] == list(prices)


@pytest.mark.parametrize("featured,new", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("available", [False, True])
def test_public_menu_and_detail_preserve_highlight_flags(menu_client, add_product, featured, new, available):
    product = add_product(name="Producto HU006", featured=featured, new=new, available=available)
    hidden = add_product(name="Producto oculto HU006", featured=True, new=True, active=False)

    listing = menu_client.get("/api/menu", params={"q": "HU006"})
    detail = menu_client.get(f"/api/menu/{product.product_id}")

    assert listing.status_code == detail.status_code == 200
    listed, = listing.json()["products"]
    assert listed["product_id"] == product.product_id
    for value in [listed, detail.json()["product"]]:
        assert value["is_featured"] is featured
        assert value["is_new"] is new
        assert value["is_available"] is available
        assert value["can_order"] is available
    assert menu_client.get(f"/api/menu/{hidden.product_id}").status_code == 404


@pytest.mark.parametrize("q,expected_names", [
    (None, ["Hamburguesa Doble", "Limonada", "Oferta 100%", "Combo_A"]),
    ("", ["Hamburguesa Doble", "Limonada", "Oferta 100%", "Combo_A"]),
    ("   ", ["Hamburguesa Doble", "Limonada", "Oferta 100%", "Combo_A"]),
    ("  DOBLE  ", ["Hamburguesa Doble"]),
    ("lim", ["Limonada"]),
    ("inexistente", []),
    ("%", ["Oferta 100%"]),
    ("_", ["Combo_A"]),
    ("' OR 1=1 --", []),
])
def test_menu_name_search_preserves_public_products(menu_client, add_product, q, expected_names):
    """HU-002: recorrido HTTP real y coincidencias en PostgreSQL temporal."""
    add_product(name="Hamburguesa Doble", available=False)
    add_product(name="Limonada")
    add_product(name="Oferta 100%")
    add_product(name="Combo_A")
    add_product(name="Hamburguesa Doble oculta", active=False)

    params = {} if q is None else {"q": q}
    response = menu_client.get("/api/menu", params=params)

    assert response.status_code == 200
    products = response.json()["products"]
    assert [product["name"] for product in products] == expected_names
    identifiers = [product["product_id"] for product in products]
    assert identifiers == sorted(set(identifiers))
    for product in products:
        assert product["is_active"] is True
        assert product["variants"]  # La búsqueda conserva las variantes.
        if product["name"] == "Hamburguesa Doble":
            assert product["is_available"] is False
            assert product["can_order"] is False


@pytest.mark.parametrize("available,flags,minimum,maximum", [
    (True, (True, True), "35.00", "49.00"),
    (False, (True, True), "35.00", "49.00"),
    (True, (False, True), "49.00", "49.00"),
    (True, (False, False), "35.00", "49.00"),
    (False, (False, True), "35.00", "49.00"),
])
def test_stored_availability_and_effective_can_order(
    menu_client, add_product, available, flags, minimum, maximum,
):
    add_product(prices=("35.00", "49.00"), available=available, flags=flags)
    response = menu_client.get("/api/menu")
    assert response.status_code == 200
    assert len(response.json()["products"]) == 1  # Agotado permanece visible.
    product = response.json()["products"][0]
    assert product["is_active"] is True
    assert product["is_available"] is available
    assert product["can_order"] is (available and any(flags))
    assert [variant["is_available"] for variant in product["variants"]] == list(flags)
    assert [variant["can_order"] for variant in product["variants"]] == [available and flag for flag in flags]
    assert (product["price_min"], product["price_max"]) == (minimum, maximum)
    assert product["has_price_range"] is (minimum != maximum)


@pytest.mark.parametrize("rows,expected", [
    ([], []),
    ([SimpleNamespace(tag_id=7, name="vegano", created_at="internal")],
     [{"tag_id": 7, "name": "vegano"}]),
])
def test_public_tags_http_contract(api_app, rows, expected):
    """Sesión simulada: comprueba ruta, delegación y serialización pública."""
    db = Mock()
    db.scalars.return_value.all.return_value = rows
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        response = client.get("/api/menu/tags")
    assert response.status_code == 200
    assert response.json() == expected
    db.scalars.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_not_called()


@pytest.mark.parametrize("error_type,expected_status", [
    (OperationalError, 503),
    (TimeoutError, 503),
    (ProgrammingError, 500),
])
def test_public_tags_errors_are_sanitized(api_app, caplog, error_type, expected_status):
    sensitive = "synthetic_secret_must_not_escape"
    error = (error_type(sensitive) if error_type is TimeoutError
             else error_type("SELECT", {}, Exception(sensitive)))
    db = Mock()
    db.scalars.side_effect = error
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        response = client.get("/api/menu/tags")
    assert response.status_code == expected_status
    assert sensitive not in response.text + caplog.text


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "1.5"])
def test_menu_tag_ids_reject_invalid_items_before_query(api_app, value):
    db = Mock()
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        response = client.get("/api/menu", params=[("tag_id", "7"), ("tag_id", value)])
    assert response.status_code == 422
    db.scalars.assert_not_called()


def test_menu_tag_ids_reach_query_with_name_filter(api_app):
    from sqlalchemy.dialects import postgresql

    db = Mock()
    db.scalars.return_value.all.return_value = []
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        response = client.get("/api/menu", params=[("q", "  DOBLE  "), ("tag_id", "7"), ("tag_id", "8")])
    assert response.status_code == 200
    assert response.json() == {"currency": "MXN", "products": []}
    compiled = db.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "DOBLE" in compiled.params.values()
    assert [7, 8] in compiled.params.values()
    assert 'JOIN "ProductTags" ON' in str(compiled)
    assert '"Products".is_active IS true' in str(compiled)


def test_public_tags_openapi(api_app):
    with TestClient(api_app) as client:
        schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/menu/tags"]["get"]
    assert not operation.get("security")
    assert {"200", "500", "503"} <= set(operation["responses"])
    body = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert body["type"] == "array"
    assert body["items"]["$ref"].endswith("/MenuTag")


def test_commercially_inactive_products_are_excluded(menu_client, add_product):
    hidden = add_product(active=False, available=True)
    visible = add_product(available=False)
    response = menu_client.get("/api/menu")
    assert response.status_code == 200
    products = response.json()["products"]
    assert [product["product_id"] for product in products] == [visible.product_id]
    assert hidden.product_id not in [product["product_id"] for product in products]
    assert products[0]["can_order"] is False


def test_product_without_variants_or_optional_metadata(menu_client, add_product):
    add_product(prices=())
    response = menu_client.get("/api/menu")
    assert response.status_code == 200
    product = response.json()["products"][0]
    assert product["variants"] == []
    assert product["description"] is None and product["image_url"] is None
    assert product["is_available"] is True and product["can_order"] is False
    assert product["price_min"] is None and product["price_max"] is None
    assert product["has_price_range"] is False


@pytest.mark.parametrize("inactive_product", [False, True])
def test_empty_public_catalog_returns_200(menu_client, menu_db, add_product, inactive_product):
    if inactive_product:
        add_product(active=False)
    with capture_sql(menu_db.connection) as statements:
        response = menu_client.get("/api/menu")
    assert response.status_code == 200
    assert response.json() == {"currency": "MXN", "products": []}
    assert len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]) == 1


def test_repeated_reads_preserve_data_and_stable_identifiers(menu_client, menu_db, add_product):
    added = [add_product(name=name, prices=("49.00", "35.00")) for name in ("Zeta", "Alfa")]

    def snapshot():
        return {
            name: [dict(row) for row in menu_db.connection.execute(
                select(table).order_by(*table.primary_key.columns),
            ).mappings()]
            for name, table in catalog_metadata.tables.items()
        }

    before = snapshot()
    first = menu_client.get("/api/menu")
    second = menu_client.get("/api/menu")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert snapshot() == before
    products = first.json()["products"]
    assert [product["product_id"] for product in products] == sorted(product.product_id for product in added)
    for product in products:
        identifiers = [variant["product_variant_id"] for variant in product["variants"]]
        assert identifiers == sorted(identifiers)


@pytest.mark.parametrize("product_count", [1, 25])
def test_menu_loads_variants_without_n_plus_one(menu_client, menu_db, add_product, product_count):
    for _ in range(product_count):
        add_product(prices=("35.00", "49.00"))
    with capture_sql(menu_db.connection) as statements:
        response = menu_client.get("/api/menu")
    assert response.status_code == 200
    assert len(response.json()["products"]) == product_count
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 4
    assert '"Products"' in selects[0] and '"ProductVariants"' in selects[1]
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"))
                   for sql in statements)


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_postgresql_decimal_precision_reaches_json(menu_client, add_product):
    exact = "90.1234567890123456789"
    add_product(prices=(exact, "129.00"))
    response = menu_client.get("/api/menu")
    assert response.status_code == 200
    product = response.json()["products"][0]
    assert product["price_min"] == product["variants"][0]["price"] == exact
    assert product["price_max"] == "129.00"


@pytest.mark.parametrize("amount,expected", [
    ("45", "45.00"), ("1.2", "1.20"), ("10.1200", "10.12"),
    ("90.1234567890123456789", "90.1234567890123456789"),
    ("1E+3", "1000.00"), ("1E-8", "0.00000001"),
    ("0", "0.00"), ("-0", "0.00"),
])
def test_decimal_serializer_preserves_value(amount, expected):
    variant = MenuVariant(
        product_variant_id=1, name="Presentación", price=Decimal(amount),
        is_available=True, can_order=True,
    )
    assert isinstance(variant.price, Decimal)
    assert variant.price == Decimal(amount)
    assert json.loads(variant.model_dump_json())["price"] == expected


@pytest.mark.parametrize("invalid", [
    None, Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), 1.1, "45.00",
])
def test_invalid_prices_never_become_zero(invalid):
    with pytest.raises(ValidationError):
        MenuVariant(product_variant_id=1, name="Presentación", price=invalid,
                    is_available=True, can_order=True)


@pytest.mark.parametrize("error_type,expected_status", [
    (OperationalError, 503), (InterfaceError, 503), (TimeoutError, 503),
    (ProgrammingError, 500), (RuntimeError, 500),
])
def test_database_errors_do_not_expose_sensitive_details(api_app, caplog, error_type, expected_status):
    sensitive = "PRIVATE_PASSWORD internal-host SQL_INTERNO SELECT * FROM sensitive_table"
    error = (error_type(sensitive) if error_type in (TimeoutError, RuntimeError)
             else error_type("SELECT SQL_INTERNO", {}, RuntimeError(sensitive)))

    class FailingSession:
        def scalars(self, statement):
            raise error

    api_app.dependency_overrides[get_db] = lambda: FailingSession()
    with TestClient(api_app) as client:
        response = client.get("/api/menu")
    assert response.status_code == expected_status
    expected_message = ("El menú no está disponible temporalmente." if expected_status == 503
                        else "No fue posible consultar el menú.")
    assert response.json() == {"detail": expected_message}
    assert caplog.records
    for marker in ("PRIVATE_PASSWORD", "internal-host", "SQL_INTERNO", "sensitive_table"):
        assert marker not in response.text
        assert marker not in caplog.text


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_postgresql_statement_error_is_sanitized(menu_client, menu_db, caplog):
    def invalid_select(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            return "SELECT * FROM hu001_missing_relation", ()
        return statement, parameters

    event.listen(menu_db.connection, "before_cursor_execute", invalid_select, retval=True)
    try:
        response = menu_client.get("/api/menu")
    finally:
        event.remove(menu_db.connection, "before_cursor_execute", invalid_select)
    assert response.status_code == 500
    assert response.json() == {"detail": "No fue posible consultar el menú."}
    assert "hu001_missing_relation" not in response.text + caplog.text


def test_openapi_and_swagger_describe_public_contract(api_app):
    with TestClient(api_app) as client:
        assert client.get("/docs").status_code == 200
        response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operation = schema["paths"]["/api/menu"]["get"]
    search = next(parameter for parameter in operation["parameters"] if parameter["name"] == "q")
    assert search["in"] == "query"
    assert search["required"] is False
    assert search["description"]
    assert {item["type"] for item in search["schema"]["anyOf"]} == {"string", "null"}
    tag_filter = next(parameter for parameter in operation["parameters"] if parameter["name"] == "tag_id")
    assert tag_filter["in"] == "query"
    assert tag_filter["required"] is False
    assert tag_filter["description"]
    array_schema = next(item for item in tag_filter["schema"]["anyOf"] if item["type"] == "array")
    assert array_schema["items"]["type"] == "integer"
    assert array_schema["items"]["exclusiveMinimum"] == 0
    assert not operation.get("security")
    assert set(operation["responses"]) == {"200", "422", "500", "503"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/MenuResponse")
    models = schema["components"]["schemas"]
    assert models["MenuVariant"]["properties"]["price"]["type"] == "string"
    assert {item["type"] for item in models["MenuProduct"]["properties"]["price_min"]["anyOf"]} == {"string", "null"}
    assert models["MenuResponse"]["properties"]["currency"]["const"] == "MXN"
    assert "is_active" not in models["MenuVariant"]["properties"]
    for model in ["MenuProduct", "MenuProductDetail"]:
        for field in ["is_featured", "is_new"]:
            assert field in models[model]["required"]
            assert models[model]["properties"][field]["type"] == "boolean"
            assert models[model]["properties"][field]["description"]
    assert models["MenuResponse"]["examples"][1] == {"currency": "MXN", "products": []}
    for example in models["MenuResponse"]["examples"]:
        MenuResponse.model_validate_json(json.dumps(example))
    assert models["MenuResponse"]["examples"][0]["products"][0]["description"] is None
    assert models["MenuResponse"]["examples"][0]["products"][0]["image_url"] is None


@pytest.mark.parametrize("origin,allowed", [
    ("http://localhost:5173", True),
    ("http://127.0.0.1:5173", False),
    ("http://localhost:3000", False),
    ("https://external.example", False),
])
def test_only_authorized_development_origin_is_allowed(api_app, origin, allowed):
    empty_session = SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: []))
    api_app.dependency_overrides[get_db] = lambda: empty_session
    with TestClient(api_app) as client:
        preflight = client.options("/api/menu", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        })
        response = client.get("/api/menu", headers={"Origin": origin})
    assert preflight.status_code == (200 if allowed else 400)
    assert response.status_code == 200
    if allowed:
        for result in (preflight, response):
            assert result.headers["access-control-allow-origin"] == origin
            assert "origin" in result.headers.get("vary", "").lower()
    else:
        for result in (preflight, response):
            assert "access-control-allow-origin" not in result.headers
    assert "GET" in preflight.headers["access-control-allow-methods"]
    assert response.json() == {"currency": "MXN", "products": []}


def test_menu_reads_catalog_written_by_hu008(api_app, sync_db, commercial_snapshot):
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["variants"][1]["stores"][0]["price"] = 129
    sync_db.service.persist_snapshot(changed)

    def isolated_db():
        with sync_db.sessions() as session:
            yield session

    api_app.dependency_overrides[get_db] = isolated_db
    with TestClient(api_app) as client:
        response = client.get("/api/menu")
    assert response.status_code == 200
    products = response.json()["products"]
    assert len(products) == 3
    assert sum(len(product["variants"]) for product in products) == 5
    burger = next(product for product in products if product["name"] == "Hamburguesa")
    assert (burger["price_min"], burger["price_max"]) == ("89.00", "129.00")
    assert next(variant for variant in burger["variants"] if variant["name"] == "Doble")["price"] == "129.00"


@pytest.fixture
def detail_catalog(menu_db, add_product):
    """Grafo con grupos compartidos, exclusivos, ajenos y vacíos en PostgreSQL aislado."""
    assert menu_db.connection.engine.url.database.startswith("mitbol_test_hu008_")
    product = add_product(prices=("89.00", "129.00"), flags=(True, False),
                          name="Hamburguesa de prueba", description="Descripción local")
    other = add_product(name="Otro producto")
    timestamp = datetime(2026, 11, 2, tzinfo=timezone.utc)

    def group(identifier, order, options):
        modifier = ProductModifier(loyverse_id=identifier, name=identifier, display_order=order)
        modifier.complements = [
            ProductComplement(loyverse_id=name, name=name, price=Decimal(price),
                              display_order=position, is_active=active, synced_at=timestamp)
            for name, price, position, active in options
        ]
        return modifier

    shared = group("Compartido", 2, [
        ("Gratis", "0.00", 1, True),
        ("Adicional", "15.50", 2, True),
        ("Inactivo", "20.00", 2, False),
    ])
    exclusive = group("Exclusivo", 2, [("Salsa", "3.00", 1, True)])
    foreign = group("Ajeno", 0, [("Ajeno opción", "8.00", 1, True)])
    empty = group("Vacío", 1, [])
    with menu_db.sessions() as session, session.begin():
        session.add_all([shared, exclusive, foreign, empty])
        session.flush()
        session.add_all([
            ProductModifierAssignment(product_variant_id=variant.product_variant_id, modifier=modifier)
            for variant, modifier in (
                (product.variants[0], exclusive), (product.variants[0], shared),
                (product.variants[1], shared), (product.variants[1], empty),
                (other.variants[0], shared), (other.variants[0], foreign),
            )
        ])
    return SimpleNamespace(product=product, other=other, shared=shared, exclusive=exclusive,
                           foreign=foreign, empty=empty)


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("prices", [("45.00",), ("89.00", "129.00"), ("0.00", "10.00")])
def test_detail_is_public_and_extends_hu001(menu_client, add_product, prices):
    product = add_product(prices=prices, description="Descripción conservada")
    response = menu_client.get(f"/api/menu/{product.product_id}")
    assert response.status_code == 200
    assert "authorization" not in response.request.headers and "cookie" not in response.request.headers
    assert set(response.json()) == {"currency", "product"}
    assert response.json()["currency"] == "MXN"
    detail = response.json()["product"]
    listed = menu_client.get("/api/menu").json()["products"][0]
    common = {**detail, "variants": [
        {key: value for key, value in variant.items() if key not in {"modifiers", "images"}}
        for variant in detail["variants"]
    ]}
    assert common == listed
    assert detail["description"] == "Descripción conservada" and detail["image_url"] is None
    assert [variant["price"] for variant in detail["variants"]] == list(prices)
    assert all(variant["modifiers"] == [] for variant in detail["variants"])
    assert set(detail) == set(listed)
    assert all(set(variant) == set(listed["variants"][0]) | {"modifiers", "images"}
               for variant in detail["variants"])


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_detail_keeps_assignments_and_shared_group_context(menu_client, detail_catalog):
    catalog = detail_catalog
    response = menu_client.get(f"/api/menu/{catalog.product.product_id}")
    assert response.status_code == 200
    first, second = response.json()["product"]["variants"]
    assert [first["product_variant_id"], second["product_variant_id"]] == [
        variant.product_variant_id for variant in catalog.product.variants
    ]
    assert [group["product_modifier_id"] for group in first["modifiers"]] == [
        catalog.shared.product_modifier_id, catalog.exclusive.product_modifier_id,
    ]
    assert [group["product_modifier_id"] for group in second["modifiers"]] == [
        catalog.empty.product_modifier_id, catalog.shared.product_modifier_id,
    ]
    assert second["modifiers"][0]["complements"] == []
    available_options = first["modifiers"][0]["complements"]
    unavailable_options = second["modifiers"][1]["complements"]
    assert [option["product_complement_id"] for option in available_options] == [
        option.product_complement_id for option in catalog.shared.complements
    ]
    assert [option["price"] for option in available_options] == ["0.00", "15.50", "20.00"]
    assert [option["is_active"] for option in available_options] == [True, True, False]
    assert [option["can_order"] for option in available_options] == [True, True, False]
    assert all(not option["can_order"] for option in unavailable_options)
    assert [{key: value for key, value in option.items() if key != "can_order"}
            for option in available_options] == [
        {key: value for key, value in option.items() if key != "can_order"}
        for option in unavailable_options
    ]
    other = menu_client.get(f"/api/menu/{catalog.other.product_id}").json()["product"]
    assert {group["product_modifier_id"] for group in other["variants"][0]["modifiers"]} == {
        catalog.foreign.product_modifier_id, catalog.shared.product_modifier_id,
    }
    assert all(option["can_order"] is option["is_active"]
               for group in other["variants"][0]["modifiers"] for option in group["complements"])
    for marker in ("loyverse_id", "synced_at", "modifier_assignments", "Ajeno"):
        assert marker not in response.text


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("available,flags,minimum,maximum", [
    (True, (True, True), "35.00", "49.00"),
    (False, (True, True), "35.00", "49.00"),
    (True, (False, True), "49.00", "49.00"),
    (True, (False, False), "35.00", "49.00"),
    (False, (False, True), "35.00", "49.00"),
])
def test_detail_availability_matches_hu001(
    menu_client, menu_db, add_product, available, flags, minimum, maximum,
):
    product = add_product(prices=("35.00", "49.00"), available=available, flags=flags)
    modifier = ProductModifier(loyverse_id="availability-group", name="Extras", display_order=1)
    modifier.complements = [ProductComplement(
        loyverse_id="availability-option", name="Opción", price=Decimal("5.00"),
        display_order=1, synced_at=product.synced_at,
    )]
    with menu_db.sessions() as session, session.begin():
        session.add_all([ProductModifierAssignment(
            product_variant_id=variant.product_variant_id, modifier=modifier,
        ) for variant in product.variants])
    response = menu_client.get(f"/api/menu/{product.product_id}")
    assert response.status_code == 200
    detail = response.json()["product"]
    assert detail["is_active"] is True and detail["is_available"] is available
    assert detail["can_order"] is (available and any(flags))
    assert (detail["price_min"], detail["price_max"]) == (minimum, maximum)
    assert detail["has_price_range"] is (minimum != maximum)
    for variant, flag in zip(detail["variants"], flags, strict=True):
        assert variant["is_available"] is flag
        assert variant["can_order"] is (available and flag)
        assert variant["modifiers"][0]["complements"][0]["can_order"] is (available and flag)


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_detail_without_variants(menu_client, add_product):
    product = add_product(prices=())
    response = menu_client.get(f"/api/menu/{product.product_id}")
    assert response.status_code == 200
    detail = response.json()["product"]
    assert detail["variants"] == []
    assert detail["description"] is None and detail["image_url"] is None
    assert detail["is_available"] is True and detail["can_order"] is False
    assert detail["price_min"] is None and detail["price_max"] is None
    assert detail["has_price_range"] is False


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_detail_missing_and_inactive_share_404(menu_client, menu_db, add_product):
    product = add_product(active=False)
    for identifier in (product.product_id, product.product_id + 100000):
        with capture_sql(menu_db.connection) as statements:
            response = menu_client.get(f"/api/menu/{identifier}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Producto no encontrado."}
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 1


@pytest.mark.parametrize("identifier", ["abc", "0", "-1", "1.5"])
def test_detail_invalid_identifier_never_queries_database(api_app, identifier):
    class UnexpectedSession:
        def scalars(self, statement):
            pytest.fail("Un ID inválido llegó a consultar el catálogo")

    api_app.dependency_overrides[get_db] = lambda: UnexpectedSession()
    with TestClient(api_app) as client:
        response = client.get(f"/api/menu/{identifier}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "product_id"]


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_detail_postgresql_preserves_variant_and_complement_precision(menu_client, menu_db, add_product):
    exact = "90.1234567890123456789"
    extra = "0.1234567890123456789"
    product = add_product(prices=(exact, "129.00"))
    modifier = ProductModifier(loyverse_id="precision-group", name="Extras", display_order=1)
    modifier.complements = [ProductComplement(
        loyverse_id="precision-option", name="Preciso", price=Decimal(extra),
        display_order=1, synced_at=product.synced_at,
    )]
    with menu_db.sessions() as session, session.begin():
        session.add(ProductModifierAssignment(
            product_variant_id=product.variants[0].product_variant_id, modifier=modifier,
        ))
    response = menu_client.get(f"/api/menu/{product.product_id}")
    assert response.status_code == 200
    detail = response.json()["product"]
    assert detail["price_min"] == detail["variants"][0]["price"] == exact
    assert detail["price_max"] == "129.00"
    assert detail["variants"][0]["modifiers"][0]["complements"][0]["price"] == extra


@pytest.mark.parametrize("invalid", [
    None, Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), 1.1, "15.00",
])
def test_detail_invalid_complement_prices_are_rejected(invalid):
    with pytest.raises(ValidationError):
        MenuComplement(product_complement_id=1, name="Opción", price=invalid,
                       display_order=1, is_active=True, can_order=True)


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("variant_count", [1, 25])
def test_detail_loads_full_tree_without_n_plus_one(menu_client, menu_db, add_product, variant_count):
    product = add_product(prices=("45.00",) * variant_count)
    with menu_db.sessions() as session, session.begin():
        for index, variant in enumerate(product.variants):
            modifier = ProductModifier(loyverse_id=f"batch-group-{index}", name=f"Grupo {index}",
                                       display_order=index)
            modifier.complements = [ProductComplement(
                loyverse_id=f"batch-option-{index}", name=f"Opción {index}", price=Decimal("0.00"),
                display_order=1, synced_at=product.synced_at,
            )]
            session.add(ProductModifierAssignment(
                product_variant_id=variant.product_variant_id, modifier=modifier,
            ))
    with capture_sql(menu_db.connection) as statements:
        response = menu_client.get(f"/api/menu/{product.product_id}")
    assert response.status_code == 200
    variants = response.json()["product"]["variants"]
    assert len(variants) == variant_count
    assert all(len(variant["modifiers"]) == 1 for variant in variants)
    assert all(len(variant["modifiers"][0]["complements"]) == 1 for variant in variants)
    # Los SAVEPOINT de la fixture aíslan las pruebas; no son consultas de catálogo.
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 6
    assert '"Products"' in selects[0] and '"ProductVariants"' in selects[1]
    assert '"ProductModifierAssignments"' in selects[2] and 'JOIN "ProductModifiers"' in selects[2]
    assert '"ProductComplements"' in selects[3]
    assert not any(sql.lstrip().upper().startswith(
        ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"),
    ) for sql in statements)


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_detail_repeated_reads_preserve_all_catalog_rows(menu_client, menu_db, detail_catalog):
    def snapshot():
        return {
            name: [dict(row) for row in menu_db.connection.execute(
                select(table).order_by(*table.primary_key.columns),
            ).mappings()]
            for name, table in catalog_metadata.tables.items()
        }

    before = snapshot()
    first = menu_client.get(f"/api/menu/{detail_catalog.product.product_id}")
    second = menu_client.get(f"/api/menu/{detail_catalog.product.product_id}")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert snapshot() == before


@pytest.mark.parametrize("error_type,expected_status", [
    (OperationalError, 503), (InterfaceError, 503), (TimeoutError, 503),
    (ProgrammingError, 500), (RuntimeError, 500),
])
def test_detail_database_errors_are_sanitized(api_app, caplog, error_type, expected_status):
    sensitive = "PRIVATE_PASSWORD internal-host SQL_INTERNO sensitive_table"
    error = (error_type(sensitive) if error_type in (TimeoutError, RuntimeError)
             else error_type("SELECT SQL_INTERNO", {}, RuntimeError(sensitive)))

    class FailingSession:
        def scalars(self, statement):
            raise error

    api_app.dependency_overrides[get_db] = lambda: FailingSession()
    with TestClient(api_app) as client:
        response = client.get("/api/menu/1")
    assert response.status_code == expected_status
    message = ("El detalle del producto no está disponible temporalmente." if expected_status == 503
               else "No fue posible consultar el detalle del producto.")
    assert response.json() == {"detail": message}
    assert caplog.records
    for marker in sensitive.split():
        assert marker not in response.text + caplog.text


@pytest.mark.parametrize("menu_db", ["postgresql"], indirect=True)
def test_detail_complement_query_error_is_sanitized(menu_client, menu_db, detail_catalog, caplog):
    def invalid_select(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and '"ProductComplements"' in statement:
            return "SELECT * FROM hu003_missing_relation", ()
        return statement, parameters

    event.listen(menu_db.connection, "before_cursor_execute", invalid_select, retval=True)
    try:
        response = menu_client.get(f"/api/menu/{detail_catalog.product.product_id}")
    finally:
        event.remove(menu_db.connection, "before_cursor_execute", invalid_select)
    assert response.status_code == 500
    assert response.json() == {"detail": "No fue posible consultar el detalle del producto."}
    assert "hu003_missing_relation" not in response.text + caplog.text


def test_detail_openapi_and_swagger_describe_contract(api_app):
    with TestClient(api_app) as client:
        assert client.get("/docs").status_code == 200
        response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operation = schema["paths"]["/api/menu/{product_id}"]["get"]
    assert not operation.get("security")
    assert set(operation["responses"]) == {"200", "404", "422", "500", "503"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/MenuProductDetailResponse"
    )
    parameter = next(param for param in operation["parameters"] if param["name"] == "product_id")
    assert parameter["in"] == "path" and parameter["required"] is True
    assert parameter["schema"]["type"] == "integer" and parameter["schema"]["minimum"] == 1
    models = schema["components"]["schemas"]
    assert models["MenuProductDetailResponse"]["properties"]["currency"]["const"] == "MXN"
    for name in ("MenuVariantDetail", "MenuComplement"):
        assert models[name]["properties"]["price"]["type"] == "string"
    assert {part["type"] for part in models["MenuProductDetail"]["properties"]["price_min"]["anyOf"]} == {
        "string", "null",
    }
    assert "is_active" not in models["MenuVariantDetail"]["properties"]
    assert "is_available" not in models["MenuComplement"]["properties"]
    assert not {"is_active", "is_available"} & models["MenuModifier"]["properties"].keys()
    for name in ("MenuProductDetail", "MenuVariantDetail", "MenuModifier", "MenuComplement"):
        assert not {"loyverse_id", "synced_at", "min_quantity", "max_quantity", "required"} & (
            models[name]["properties"].keys()
        )
    for example in models["MenuProductDetailResponse"]["examples"]:
        MenuProductDetailResponse.model_validate_json(json.dumps(example))
        assert example["product"]["description"] is None
        assert example["product"]["image_url"] is None


def test_detail_reads_catalog_written_by_hu008(api_app, sync_db, commercial_snapshot):
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["variants"][1]["stores"][0]["price"] = 129
    sync_db.service.persist_snapshot(changed)

    def isolated_db():
        with sync_db.sessions() as session:
            yield session

    api_app.dependency_overrides[get_db] = isolated_db
    with TestClient(api_app) as client:
        listing = client.get("/api/menu").json()
        details = [client.get(f'/api/menu/{product["product_id"]}') for product in listing["products"]]
    assert all(response.status_code == 200 for response in details)
    products = {response.json()["product"]["name"]: response.json()["product"] for response in details}
    burger = products["Hamburguesa"]
    assert [variant["price"] for variant in burger["variants"]] == ["89.00", "129.00"]
    for variant in burger["variants"]:
        assert [group["name"] for group in variant["modifiers"]] == ["Extras"]
        assert [option["name"] for option in variant["modifiers"][0]["complements"]] == [
            "Queso", "Tocino", "Sin cebolla",
        ]
    for variant in products["Limonada"]["variants"]:
        assert [group["name"] for group in variant["modifiers"]] == ["Hierbas"]
        assert [option["name"] for option in variant["modifiers"][0]["complements"]] == ["Chía", "Menta"]
    assert products["Papas"]["variants"][0]["modifiers"] == []
