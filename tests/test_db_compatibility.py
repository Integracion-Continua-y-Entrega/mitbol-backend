"""Revisiones 0008–0012 y perfiles existentes, sólo en PostgreSQL desechable."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.db import writer_policy
from app.db.connection import DatabaseTargetError
from app.db.schema_compatibility import RECENT_REVISIONS, validate_bridge_schema
from app.db.worker_policy import validate_worker_connection
from app.cli.sync_discounts import preflight as discount_preflight
from app.cli.sync_catalog import _table_counts
from app.models import (
    AccountSession, AuditEvent, AuthBase, AuthRateLimit, AuthRateLimitPolicy, Base,
    CustomerCoupon, Discount, Order, PasswordResetToken, Person, PersonalAccount,
    ProductComplement, ProductModifier, ProductModifierAssignment, RestaurantOrderSettings,
)
from app.models.coupon import CouponBase
from app.models.order import OrderBase
from app.models.password_recovery import PasswordRecoveryBase
from app.models.sync_run import SyncBase
from app.services.persistent_auth_limiter import PostgresAttemptLimiter
from app.services.catalog_safety import inspect_catalog_operator
from app.services.sync_service import validate_catalog_revision
from test_admin_writer import (
    backend as writer_backend, denied, execute_script, writer_database, VERIFY,
)
from test_auth_admin_api import backend as catalog_backend, product_path
from test_auth_api import backend as auth_backend, csrf_headers, login, NEW_PASSWORD, password_hash
from test_auth_persistence import auth_database, config_for
from test_customer_registration import payload as registration_payload, register
from test_password_recovery import request_recovery, reset


SQL = Path(__file__).resolve().parents[1] / "docs/database"
DELTA = "update_admin_writer_accounts_orders.sql"


def approved(filename, marker="approved_admin_write", value="db-compat-0012-reviewed"):
    return (SQL / filename).read_text(encoding="utf-8").replace(
        "BEGIN;", f"BEGIN;\nSET LOCAL mitbol.{marker}='{value}';", 1)


@pytest.fixture(scope="module")
def compatible_database(writer_database):
    r = writer_database
    workers = {}
    with r.owner.begin() as db:
        command.upgrade(config_for(db), "20261115_0008")
    for name, marker, value in (
        ("update_admin_writer_discounts.sql", "approved_admin_write", "hu009-reviewed"),
        ("update_admin_writer_sync.sql", "approved_admin_write", "hu010-reviewed"),
        ("update_catalog_categories.sql", "approved_category_write", "hu008-categories-reviewed"),
    ):
        execute_script(r.owner, approved(name, marker, value))
    try:
        # Copias efímeras de los perfiles existentes, usando sus scripts actuales.
        for role, filename, marker, value in (
            ("mitbol_catalog_worker", "prepare_catalog_worker.sql", "approved_worker_write", "hu010-worker-reviewed"),
            ("mitbol_discount_worker", "prepare_discount_worker.sql", "approved_discount_worker_write", "hu009-discount-worker-reviewed"),
        ):
            execute_script(r.owner, approved(filename, marker, value))
            if r.owner.url.password:
                with r.owner.begin() as db:
                    db.exec_driver_sql(f"ALTER ROLE {role} PASSWORD 'synthetic-compat-worker-fixture'")
            workers[role] = create_engine(r.owner.url.set(username=role,
                password="synthetic-compat-worker-fixture" if r.owner.url.password else None), hide_parameters=True)
        for revision in RECENT_REVISIONS:
            with r.owner.begin() as db:
                command.upgrade(config_for(db), revision)
            execute_script(r.owner, approved(DELTA))
            for engine, filename in (
                (r.writer, "verify_admin_writer_local.sql"),
                (workers["mitbol_catalog_worker"], "verify_catalog_worker.sql"),
                (workers["mitbol_discount_worker"], "verify_discount_worker.sql"),
            ):
                execute_script(engine, (SQL / filename).read_text(encoding="utf-8"))
                with engine.connect() as db:
                    assert validate_bridge_schema(db) == revision
            reader_sql = (SQL / "verify_render_reader.sql").read_text(encoding="utf-8")
            reader_sql = "\n".join(line for line in reader_sql.splitlines() if not line.startswith("\\"))
            execute_script(r.reader, reader_sql.replace("mitbol_cloud_dev", r.owner.url.database))
        yield SimpleNamespace(**vars(r), workers=workers)
    finally:
        for role, engine in workers.items():
            engine.dispose()
            with r.owner.begin() as db:
                db.exec_driver_sql(f"DROP OWNED BY {role}")
                db.exec_driver_sql(f"DROP ROLE {role}")


def test_sequential_migrations_contracts_and_reverse_to_0008(compatible_database):
    r = compatible_database
    with r.owner.connect() as db, db.begin() as transaction:
        assert db.scalar(select(RestaurantOrderSettings.accepting_orders)) is False
        assert not compare_metadata(MigrationContext.configure(db), [Base.metadata, AuthBase.metadata,
            SyncBase.metadata, OrderBase.metadata, PasswordRecoveryBase.metadata, CouponBase.metadata])
        for revision in reversed(("20261115_0008", *RECENT_REVISIONS[:-1])):
            command.downgrade(config_for(db), revision)
            assert validate_bridge_schema(db) == revision
        assert not {"Orders", "OrderDetails", "OrderDetailComplements", "RestaurantOrderSettings",
                    "PasswordResetTokens", "CustomerCoupons"} & set(inspect(db).get_table_names())
        for revision in RECENT_REVISIONS:
            command.upgrade(config_for(db), revision)
            assert validate_bridge_schema(db) == revision
        assert db.scalar(select(RestaurantOrderSettings.accepting_orders)) is False
        transaction.rollback()  # También conserva las ACL previas del ensayo.


def test_existing_delta_is_explicit_idempotent_and_strict_profiles_work(compatible_database, monkeypatch):
    r = compatible_database
    with pytest.raises(DBAPIError, match="RECENT_NOT_APPROVED"):
        execute_script(r.owner, (SQL / DELTA).read_text(encoding="utf-8"))
    with pytest.raises(DBAPIError, match="RECENT_WRONG_TARGET_OR_OPERATOR"):
        execute_script(r.writer, approved(DELTA))
    execute_script(r.owner, approved(DELTA))
    execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))
    source = writer_policy.writer_policy_sql().replace("mitbol_cloud_dev", r.owner.url.database)
    monkeypatch.setattr(writer_policy, "writer_policy_sql", lambda: source)
    raw = r.writer.raw_connection()
    try:
        writer_policy.validate_writer_connection(raw.driver_connection)
    finally:
        raw.rollback()
        raw.close()
    with r.workers["mitbol_catalog_worker"].connect() as db:
        db.exec_driver_sql("SET TRANSACTION READ ONLY")
        validate_worker_connection(db, SimpleNamespace(validate_connection=lambda connection: None))
        assert validate_catalog_revision(db) == RECENT_REVISIONS[-1]
        assert "Categories" in _table_counts(db)
        assert inspect_catalog_operator(db)["write_permissions_verified"] is True
    result = discount_preflight(r.workers["mitbol_discount_worker"], expected_database=r.owner.url.database)
    assert result["revision"] == RECENT_REVISIONS[-1] and result["loyverse_contacted"] is False


@pytest.mark.parametrize("table", ["Persons", "PersonalAccounts", "AccountSessions", "PasswordResetTokens",
                                   "Orders", "OrderDetails", "OrderDetailComplements", "CustomerCoupons",
                                   "RestaurantOrderSettings"])
def test_reader_and_workers_have_no_access_to_private_data(compatible_database, table):
    r = compatible_database
    for engine in (r.reader, *r.workers.values()):
        denied(engine, f'SELECT * FROM public."{table}"')


@pytest.mark.parametrize("statement", [
    'UPDATE "PersonalAccounts" SET is_active=true',
    'UPDATE "PersonalAccounts" SET email=email',
    'UPDATE "PersonalAccounts" SET person_id=person_id',
    'UPDATE "Persons" SET person_id=person_id',
    'UPDATE "Persons" SET created_at=created_at',
    'INSERT INTO "Employees" DEFAULT VALUES',
    'INSERT INTO "EmployeeRoles" DEFAULT VALUES',
    'UPDATE "Roles" SET code=code',
    'UPDATE "RestaurantOrderSettings" SET accepting_orders=true',
    'UPDATE "Orders" SET total=total', 'DELETE FROM "Orders"',
    'UPDATE "OrderDetails" SET quantity=quantity', 'DELETE FROM "OrderDetails"',
    'UPDATE "OrderDetailComplements" SET unit_price=unit_price',
    'INSERT INTO "CustomerCoupons" DEFAULT VALUES',
    'UPDATE "CustomerCoupons" SET status=\'redeemed\'', 'DELETE FROM "CustomerCoupons"',
    'SELECT nextval(\'public."CustomerCoupons_customer_coupon_id_seq"\')',
    'UPDATE "PasswordResetTokens" SET account_id=account_id',
])
def test_writer_permissions_do_not_expand_administrative_or_commercial_access(compatible_database, statement):
    denied(compatible_database.writer, statement)


@pytest.mark.parametrize("statement", [
    'ALTER TABLE "Orders" DROP CONSTRAINT "fk_Orders_account_id"',
    'DROP INDEX "ix_CustomerCoupons_account_coupon"',
    'ALTER TABLE "PasswordResetTokens" ENABLE ROW LEVEL SECURITY',
])
def test_current_schema_contract_rejects_missing_relations_indexes_and_rls(compatible_database, statement):
    with compatible_database.owner.connect() as db, db.begin() as transaction:
        db.exec_driver_sql(statement)
        with pytest.raises(DatabaseTargetError):
            validate_bridge_schema(db)
        transaction.rollback()


@pytest.mark.parametrize("role,table,column,operation,verifier", [
    ("mitbol_admin_writer", "RestaurantOrderSettings", "accepting_orders", "UPDATE", "verify_admin_writer_local.sql"),
    ("mitbol_admin_writer", "CustomerCoupons", "status", "UPDATE", "verify_admin_writer_local.sql"),
    ("mitbol_catalog_worker", "PersonalAccounts", "email", "SELECT", "verify_catalog_worker.sql"),
    ("mitbol_render_reader", "Orders", "requester_name", "SELECT", "verify_render_reader.sql"),
])
def test_verifiers_detect_excess_permissions_on_recent_features(compatible_database, role, table, column, operation, verifier):
    r = compatible_database
    engine = {"mitbol_admin_writer": r.writer, "mitbol_render_reader": r.reader, **r.workers}[role]
    source = (SQL / verifier).read_text(encoding="utf-8").replace("mitbol_cloud_dev", r.owner.url.database)
    source = "\n".join(line for line in source.splitlines() if not line.startswith("\\"))
    with r.owner.begin() as db:
        db.exec_driver_sql(f'GRANT {operation} ({column}) ON public."{table}" TO {role}')
    try:
        with pytest.raises(DBAPIError):
            execute_script(engine, source)
    finally:
        with r.owner.begin() as db:
            db.exec_driver_sql(f'REVOKE {operation} ({column}) ON public."{table}" FROM {role}')
    execute_script(engine, source)


@pytest.fixture
def backend(compatible_database, writer_backend):
    b = writer_backend
    limiter_config = b.config.model_copy(update={"auth_limit_secret": SecretStr("synthetic-compat-limiter-key-2026")})
    b.service.limiter = PostgresAttemptLimiter(b.service.transaction, limiter_config)
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(Order))
            db.execute(delete(CustomerCoupon))
            db.execute(delete(Discount))
            db.execute(delete(PasswordResetToken))
            db.execute(delete(AuditEvent))
            db.execute(delete(AccountSession))
            db.execute(delete(AuthRateLimit))
            db.execute(delete(AuthRateLimitPolicy))
            db.execute(delete(PersonalAccount).where(PersonalAccount.account_id.not_in([v[0] for v in b.ids.values()])))
            db.execute(delete(Person).where(Person.person_id.not_in([v[1] for v in b.ids.values()])))
            db.get(RestaurantOrderSettings, 1).accepting_orders = False


def test_registration_profile_recovery_login_and_permissions_under_writer(backend):
    b = backend
    data = registration_payload()
    response = register(b, data)
    assert response.status_code == 201, response.text
    signed_in = login(b, email=data["email"], password=data["password"])
    assert signed_in.status_code == 200 and signed_in.json()["user"]["roles"] == []
    assert b.client.get("/api/auth/profile").status_code == 200
    assert b.client.patch("/api/auth/profile", json={"name": "Alias"}).status_code == 403
    updated = b.client.patch("/api/auth/profile", headers=csrf_headers(signed_in),
                             json={"name": "Perfil actualizado", "phone": "2281234567"})
    assert updated.status_code == 200 and updated.json()["name"] == "Perfil actualizado", updated.text
    with b.sessions() as db:
        assert db.get(Person, response.json()["user"]["person_id"]).name == "Perfil actualizado"
    assert b.client.patch(product_path(b), headers=csrf_headers(signed_in), json={"description": "Forbidden"}).status_code == 403
    assert register(b, data).status_code == 409
    deliveries = []
    b.service.recovery_url = "http://localhost:5173/recuperar"
    b.service.recovery_delivery = deliveries.append
    assert request_recovery(b, data["email"]).status_code == 202
    assert request_recovery(b, data["email"]).status_code == 202  # UPDATE del token existente.
    assert reset(b, deliveries[-1].token).status_code == 204
    assert b.client.get("/api/auth/me").status_code == 401
    assert login(b, email=data["email"], password=NEW_PASSWORD).status_code == 200
    assert reset(b, deliveries[-1].token).status_code == 400
    for role in ("admin", "staff"):
        employee = login(b, role)
        assert employee.status_code == 200 and employee.json()["user"]["roles"] == [role]
        assert b.client.patch(product_path(b), headers=csrf_headers(employee),
                            json={"description": "Permitido"}).status_code == (200 if role == "admin" else 403)


@pytest.mark.parametrize("registered", [False, True])
def test_orders_tracking_cancellation_and_coupon_selection_under_writer(backend, registered):
    b = backend
    with b.sessions.begin() as db:
        modifier = ProductModifier(loyverse_id=uuid4().hex, name="Extra sintético", display_order=0, synced_at=b.now.value)
        option = ProductComplement(modifier=modifier, loyverse_id=uuid4().hex, name="Extra sintético",
                                   price=Decimal("5.50"), display_order=0, synced_at=b.now.value)
        db.add(option)
        db.flush()
        db.add(ProductModifierAssignment(product_variant_id=b.variant_id, product_modifier_id=modifier.product_modifier_id))
        discount = Discount(loyverse_id=uuid4().hex, name="Beneficio sintético", discount_type="FIXED_AMOUNT",
                            value=Decimal("10.00"), scope="unknown", is_active=True)
        db.add(discount)
        db.flush()
        coupon = CustomerCoupon(account_id=b.ids["customer"][0], name="Cupón de prueba", discount_id=discount.discount_id,
            starts_at=b.now.value-timedelta(days=1), expires_at=b.now.value+timedelta(days=1), conditions={"scope":"order"})
        db.add(coupon)
    cart = {"items": [{"product_id": b.product_id, "product_variant_id": b.variant_id,
                       "quantity": 2, "complement_ids": [option.product_complement_id]}]}
    body = {"cart": cart, "data": {"modality": "dine_in", "name": "Alias privado"}}
    headers = {"Idempotency-Key": str(uuid4())}
    if registered:
        signed_in = login(b, "customer")
        headers.update(csrf_headers(signed_in))
        assert b.client.get("/api/auth/coupons").status_code == 200
        selected = b.client.post("/api/auth/coupons/select", headers=csrf_headers(signed_in),
            json={"cart": cart, "modality": "dine_in", "customer_coupon_id": coupon.customer_coupon_id})
        assert selected.status_code == 200 and selected.json()["benefit_amount"] == "10.00", selected.text
    closed = b.client.post("/api/orders", headers=headers, json=body)
    assert closed.status_code == 409, closed.text
    with b.sessions.begin() as db:
        db.get(RestaurantOrderSettings, 1).accepting_orders = True  # Sólo datos aislados.
    created = b.client.post("/api/orders", headers=headers, json=body)
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["total"] == "210.00" and receipt["discount_total"] == "0.00"
    assert b.client.post("/api/orders", headers=headers, json=body).json() == receipt
    if registered:
        history = b.client.get("/api/auth/orders")
        assert history.status_code == 200 and history.json()["total"] == 1
        assert history.json()["items"][0]["order_id"] == receipt["order_id"]
        detail = b.client.get(f'/api/auth/orders/{receipt["order_id"]}')
        assert detail.status_code == 200 and detail.json()["items"] == receipt["items"], detail.text
        assert detail.json()["can_cancel"] is True
    if not registered:
        access = receipt["tracking"]
        headers = {"Authorization": f'Bearer {access["token"]}'}
        assert b.client.get(access["path"]).status_code == 404
        tracked = b.client.get(access["path"], headers=headers)
        assert tracked.status_code == 200 and "Alias privado" not in tracked.text
    cancelled = b.client.post(f'/api/orders/{receipt["order_id"]}/cancel', headers=headers, json={})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "Cancelado", cancelled.text
    with b.sessions() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        assert order.status == "Cancelado" and len(order.details) == 1 and len(order.details[0].complements) == 1
        assert db.get(CustomerCoupon, coupon.customer_coupon_id).status == "available"
