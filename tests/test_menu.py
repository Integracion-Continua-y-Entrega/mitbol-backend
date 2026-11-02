"""Contrato de HU-001 con SQLite efímero y PostgreSQL aislado, sin escribir en mitbol_dev."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError, TimeoutError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.models import Base, Product, ProductVariant
from app.schemas.menu import MenuResponse, MenuVariant


@pytest.fixture(params=["sqlite", "postgresql"])
def menu_db(request):
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
    else:
        # Esta fixture existente crea mitbol_test_hu008_<id>, nunca usa mitbol_dev para escribir.
        engine = request.getfixturevalue("sync_test_database")
        assert engine.url.database.startswith("mitbol_test_hu008_")
    try:
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
    finally:
        if request.param == "sqlite":
            engine.dispose()


@pytest.fixture
def add_product(menu_db):
    def add(*, prices=("45.00",), available=True, active=True, flags=None,
            name="Producto de prueba", description=None):
        timestamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
        product = Product(
            loyverse_id="hu001-product-" + uuid4().hex, name=name,
            description=description, is_active=active, is_available=available,
            synced_at=timestamp,
        )
        product.variants = [
            ProductVariant(
                loyverse_id="hu001-variant-" + uuid4().hex,
                name=f"Presentación {index + 1}", price=Decimal(price),
                is_available=flags[index] if flags is not None else True,
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


@pytest.mark.parametrize("available,flags,minimum,maximum", [
    (True, (True, True), "35.00", "49.00"),
    (False, (True, True), "35.00", "49.00"),
    (True, (False, True), "49.00", "49.00"),
    (True, (False, False), "35.00", "49.00"),
    (False, (False, True), "49.00", "49.00"),
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
            for name, table in Base.metadata.tables.items()
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
    assert len(selects) == 2
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
    assert not operation.get("security")
    assert set(operation["responses"]) == {"200", "500", "503"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/MenuResponse")
    models = schema["components"]["schemas"]
    assert models["MenuVariant"]["properties"]["price"]["type"] == "string"
    assert {item["type"] for item in models["MenuProduct"]["properties"]["price_min"]["anyOf"]} == {"string", "null"}
    assert models["MenuResponse"]["properties"]["currency"]["const"] == "MXN"
    assert "is_active" not in models["MenuVariant"]["properties"]
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
