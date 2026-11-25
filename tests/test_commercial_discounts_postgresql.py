"""Resumen, solicitudes y migración HU-019 únicamente en PostgreSQL desechable."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from uuid import UUID, uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import delete, event, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.schema_compatibility import validate_bridge_schema
from app.models import (
    AuthBase, Base, CommercialDiscountAssignment, CommercialDiscountBase, CouponBase, CustomerCoupon,
    Discount, Order, OrderBase, OrderDiscountSnapshot, PasswordRecoveryBase, ProductComplement, ProductVariant, RestaurantOrderSettings,
)
from app.models.sync_run import SyncBase
from test_auth_persistence import auth_database, config_for
from test_auth_api import backend as auth_backend, csrf_headers, login, password_hash
from test_auth_admin_api import backend as catalog_backend
from test_admin_writer import backend as writer_backend, denied, execute_script, writer_database, VERIFY
from test_db_compatibility import backend as compatibility_backend, compatible_database, approved, DELTA, SQL
from test_commercial_discounts import conditions
from test_cart import item
from test_menu import add_product, detail_catalog
from test_order_submission import menu_db as order_menu_db, order_client, payload, submit


REVISION = "20261120_0013"


@pytest.fixture
def menu_db(order_menu_db):
    command.upgrade(config_for(order_menu_db.connection), REVISION)
    return order_menu_db


def add_discount(sessions, product_ids, *, kind="FIXED_PERCENT", value="10", rules=None):
    with sessions.begin() as db:
        discount = Discount(loyverse_id=uuid4().hex, name="Beneficio comercial sintético", discount_type=kind,
                            value=Decimal(value), scope="unknown", is_active=True)
        db.add(discount)
        db.flush()
        db.add_all([CommercialDiscountAssignment(discount_id=discount.discount_id, product_id=product_id,
            conditions=conditions() if rules is None else rules) for product_id in product_ids])
    return discount


def test_summary_and_submission_recalculate_backend_prices_and_keep_history(order_client, menu_db, detail_catalog):
    c = detail_catalog
    discount = add_discount(menu_db.sessions, [c.product.product_id])
    body = payload(c)
    first = order_client.post("/api/cart/summary", json=body["cart"])
    assert first.status_code == 200, first.text
    assert first.json()["subtotal"] == "344.00" and first.json()["discount_total"] == "17.80"
    assert first.json()["total"] == "326.20"
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[0].product_variant_id).price = Decimal("100")
    key = uuid4()
    created = submit(order_client, body, key=key)
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["subtotal"] == "366.00" and receipt["discount_total"] == "20.00" and receipt["total"] == "346.00"
    with menu_db.sessions() as db:
        snapshot = db.get(OrderDiscountSnapshot, UUID(receipt["order_id"]))
        assert snapshot.calculation["applied_discounts"] == receipt["applied_discounts"]
        assert db.get(Order, UUID(receipt["order_id"])).total == Decimal("346.00")
    with menu_db.sessions.begin() as db:
        row = db.get(Discount, discount.discount_id)
        row.value, row.name, row.is_active = Decimal("90"), "Nombre posterior", False
        db.execute(delete(CommercialDiscountAssignment))
    assert submit(order_client, body, key=key).json() == receipt
    tracked = order_client.get(receipt["tracking"]["path"], headers={"Authorization": f'Bearer {receipt["tracking"]["token"]}'})
    assert tracked.status_code == 200 and tracked.json()["applied_discounts"] == receipt["applied_discounts"]
    assert tracked.json()["total"] == receipt["total"]
    cancelled = order_client.post(f'/api/orders/{receipt["order_id"]}/cancel', json={},
        headers={"Authorization": f'Bearer {receipt["tracking"]["token"]}'})
    assert cancelled.status_code == 200
    after = order_client.get(receipt["tracking"]["path"], headers={"Authorization": f'Bearer {receipt["tracking"]["token"]}'})
    assert after.json()["status"] == "Cancelado" and after.json()["applied_discounts"] == receipt["applied_discounts"]


def test_active_unassigned_discount_is_reported_but_never_applied(order_client, menu_db, detail_catalog):
    discount = add_discount(menu_db.sessions, [])
    response = order_client.post("/api/cart/summary", json=payload(detail_catalog)["cart"])
    assert response.status_code == 200 and response.json()["total"] == "344.00"
    assert response.json()["applied_discounts"] == []
    assert response.json()["pending_discounts"] == [{"discount_id": discount.discount_id,
        "name": discount.name,"reason":"eligibility_pending"}]


@pytest.mark.parametrize("base,extra,quantity,expected_subtotal,expected_discount,expected_total", [
    ("0.335", "0.335", 3, "2.01", "0.58", "1.43"),
    ("0.335", "0.336", 3, "2.01", "0.58", "1.43"),
    ("0.0001", "0.0001", 10000, "2.00", "0.58", "1.42"),
    ("0.001", "0.001", 1, "0.00", "0.00", "0.00"),
])
def test_fractional_prices_share_summary_submission_and_history_without_changing_exact_details(
    order_client, menu_db, detail_catalog, base, extra, quantity, expected_subtotal, expected_discount, expected_total,
):
    c = detail_catalog
    option_id = c.shared.complements[1].product_complement_id
    variant_id = c.product.variants[0].product_variant_id
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, variant_id).price = Decimal(base)
        db.get(ProductComplement, option_id).price = Decimal(extra)
    discounts = [add_discount(menu_db.sessions,[c.product.product_id],value="20"),
                 add_discount(menu_db.sessions,[c.product.product_id],value="10"),
                 add_discount(menu_db.sessions,[c.product.product_id],kind="FIXED_AMOUNT",value="0.02")]
    identifiers = [row.discount_id for row in discounts]
    with menu_db.sessions.begin() as db:
        for row in db.scalars(select(CommercialDiscountAssignment)):
            row.conditions = conditions(include_complements=True,
                compatible_discount_ids=[identifier for identifier in identifiers if identifier != row.discount_id])
    body = payload(c)
    body["cart"]["items"] = [item(c.product,quantity=quantity,complements=[option_id])]
    response = order_client.post("/api/cart/summary",json=body["cart"])
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["subtotal"] == expected_subtotal and summary["discount_total"] == expected_discount
    assert summary["total"] == expected_total and summary["pending_discounts"] == []
    assert sum(Decimal(row["subtotal"]) for row in summary["items"]) == Decimal(expected_subtotal)
    assert sum(Decimal(row["amount"]) for row in summary["applied_discounts"]) == Decimal(expected_discount)
    key = uuid4()
    created = submit(order_client,body,key=key)
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert {name:receipt[name] for name in summary} == summary
    with menu_db.sessions() as db:
        order = db.get(Order,UUID(receipt["order_id"]))
        detail, = order.details
        assert detail.base_price == Decimal(base) and detail.complements_total == Decimal(extra)
        assert detail.subtotal == detail.unit_total * quantity == (Decimal(base) + Decimal(extra)) * quantity
        assert order.subtotal == Decimal(expected_subtotal) and order.total == Decimal(expected_total)
        snapshot = db.get(OrderDiscountSnapshot,order.order_id)
        assert snapshot.calculation["line_subtotals"] == [expected_subtotal]
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant,variant_id).price = Decimal("100")
        db.get(ProductComplement,option_id).price = Decimal("50")
        for row in db.scalars(select(Discount)):
            row.value = Decimal("99")
    assert submit(order_client,body,key=key).json() == receipt
    tracked = order_client.get(receipt["tracking"]["path"],
        headers={"Authorization": f'Bearer {receipt["tracking"]["token"]}'})
    assert tracked.status_code == 200, tracked.text
    assert {name:tracked.json()[name] for name in summary} == summary


def test_mvp_stacking_is_shared_by_summary_submission_and_historical_snapshot(order_client, menu_db, detail_catalog):
    body = payload(detail_catalog)
    product_ids = [row["product_id"] for row in body["cart"]["items"]]
    fixed = add_discount(menu_db.sessions,product_ids,kind="FIXED_AMOUNT",value="5")
    first = add_discount(menu_db.sessions,product_ids,value="20")
    second = add_discount(menu_db.sessions,product_ids,value="10")
    identifiers = [fixed.discount_id,first.discount_id,second.discount_id]
    with menu_db.sessions.begin() as db:
        for row in db.scalars(select(CommercialDiscountAssignment)):
            row.conditions = {"include_complements":False,
                "compatible_discount_ids":[identifier for identifier in identifiers if identifier != row.discount_id]}
    response = order_client.post("/api/cart/summary",json=body["cart"])
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["subtotal"] == "344.00" and summary["discount_total"] == "92.64" and summary["total"] == "251.36"
    assert [row["discount_id"] for row in summary["applied_discounts"]] == [first.discount_id,second.discount_id,fixed.discount_id]
    assert [row["amount"] for row in summary["applied_discounts"]] == ["62.60","25.04","5.00"]
    assert summary["pending_discounts"] == [] and summary["discounts_status"] == "applied"
    created = submit(order_client,body)
    assert created.status_code == 201, created.text
    assert {key:created.json()[key] for key in summary} == summary
    with menu_db.sessions() as db:
        order_id = UUID(created.json()["order_id"])
        assert db.get(Order,order_id).total == Decimal("251.36")
        assert db.get(OrderDiscountSnapshot,order_id).calculation["applied_discounts"] == summary["applied_discounts"]


def test_snapshot_insert_failure_rolls_back_the_entire_order(order_client, menu_db, detail_catalog):
    add_discount(menu_db.sessions, [detail_catalog.product.product_id])
    body, key = payload(detail_catalog), uuid4()
    def fail(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith('INSERT INTO "OrderDiscountSnapshots"'):
            raise OperationalError("synthetic-private-error", {}, Exception("synthetic"))
    event.listen(menu_db.connection, "before_cursor_execute", fail)
    try:
        failed = submit(order_client, body, key=key)
    finally:
        event.remove(menu_db.connection, "before_cursor_execute", fail)
    assert failed.status_code == 503 and "synthetic" not in failed.text
    with menu_db.sessions() as db:
        assert not db.scalars(select(Order)).all() and not db.scalars(select(OrderDiscountSnapshot)).all()
    assert submit(order_client, body, key=key).status_code == 201


def test_migration_is_additive_preserves_existing_rows_and_roundtrips(empty_postgresql_connection):
    db = empty_postgresql_connection
    config = config_for(db)
    command.upgrade(config, "20261119_0012")
    tables_before = set(inspect(db).get_table_names())
    discount_id = db.scalar(Discount.__table__.insert().values(loyverse_id="historical-discount", name="Conservar",
        discount_type="FIXED_AMOUNT", value=5, scope="unknown").returning(Discount.discount_id))
    legacy_id = uuid4()
    db.execute(Order.__table__.insert().values(order_id=legacy_id, idempotency_key=uuid4(), request_hash="0"*64,
        modality="dine_in", requester_name="Histórico", subtotal=10, discount_total=0, total=10))
    command.upgrade(config, REVISION)
    assert set(inspect(db).get_table_names()) == tables_before | {"CommercialDiscountAssignments","OrderDiscountSnapshots"}
    assert validate_bridge_schema(db) == REVISION
    assert not compare_metadata(MigrationContext.configure(db), [Base.metadata,AuthBase.metadata,SyncBase.metadata,
        OrderBase.metadata,PasswordRecoveryBase.metadata,CouponBase.metadata,CommercialDiscountBase.metadata])
    assert {fk["referred_table"] for fk in inspect(db).get_foreign_keys("CommercialDiscountAssignments")} == {"Products","Discounts"}
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(CommercialDiscountAssignment.__table__.insert().values(discount_id=discount_id, product_id=999999))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(OrderDiscountSnapshot.__table__.insert().values(order_id=uuid4(), calculation={}))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(OrderDiscountSnapshot.__table__.insert().values(order_id=legacy_id, calculation=[]))
    command.downgrade(config, "20261119_0012")
    assert set(inspect(db).get_table_names()) == tables_before
    assert db.scalar(select(Order.total).where(Order.order_id==legacy_id)) == 10
    assert db.scalar(select(Discount.name).where(Discount.discount_id==discount_id)) == "Conservar"
    command.upgrade(config, REVISION)
    assert db.execute(select(OrderDiscountSnapshot)).all() == []


def test_offline_migration_contains_no_business_data_permissions_or_changes_to_existing_tables():
    output = StringIO()
    command.upgrade(config_for(output=output), f"20261119_0012:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 2
    assert not any(value in sql for value in ("ALTER TABLE", "DROP TABLE", "GRANT ", "CREATE ROLE", 'INSERT INTO "Discounts"'))


@pytest.fixture
def restricted_backend(compatibility_backend):
    b = compatibility_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), REVISION)
    execute_script(b.roles.owner, approved(DELTA))
    try:
        yield b
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db), "20261119_0012")


def test_public_summary_and_guest_submission_use_existing_restricted_writer_without_opening_reader_permissions(restricted_backend, compatible_database):
    b = restricted_backend
    add_discount(b.sessions, [b.product_id])
    cart = {"items":[{"product_id":b.product_id,"product_variant_id":b.variant_id,"quantity":2}]}
    response = b.client.post("/api/cart/summary", json=cart)
    assert response.status_code == 200 and response.json()["total"] == "179.10", response.text
    assert "cookie" not in response.request.headers
    execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
    for role, filename in (("mitbol_catalog_worker", "verify_catalog_worker.sql"),
                           ("mitbol_discount_worker", "verify_discount_worker.sql")):
        worker = compatible_database.workers[role]
        execute_script(worker, (SQL/filename).read_text(encoding="utf-8"))
        with worker.connect() as db:
            assert validate_bridge_schema(db) == REVISION
    for statement in ('SELECT * FROM "Discounts"','SELECT * FROM "CommercialDiscountAssignments"','SELECT * FROM "OrderDiscountSnapshots"'):
        denied(b.roles.reader, statement)
    for statement in ('UPDATE "CommercialDiscountAssignments" SET conditions=NULL',
                      'UPDATE "OrderDiscountSnapshots" SET calculation=\'{}\''):
        denied(b.roles.writer, statement)
    with b.sessions.begin() as db:
        db.get(RestaurantOrderSettings,1).accepting_orders=True
    created = submit(b.client,{"cart":cart,"data":{"modality":"dine_in","name":"Invitado"}})
    assert created.status_code==201 and created.json()["total"]==response.json()["total"], created.text


def test_coupon_preview_keeps_commercial_summary_but_does_not_invent_stacking_or_redeem(restricted_backend):
    b = restricted_backend
    add_discount(b.sessions,[b.product_id])
    personal = add_discount(b.sessions,[],kind="FIXED_AMOUNT",value="10")
    with b.sessions.begin() as db:
        coupon = CustomerCoupon(account_id=b.ids["customer"][0],name="Cupón sintético",discount_id=personal.discount_id,
            starts_at=b.now.value-timedelta(days=1),expires_at=b.now.value+timedelta(days=1),conditions={"scope":"order"})
        db.add(coupon)
    signed_in = login(b,"customer")
    cart = {"items":[{"product_id":b.product_id,"product_variant_id":b.variant_id,"quantity":1}]}
    selected = b.client.post("/api/auth/coupons/select",headers=csrf_headers(signed_in),
        json={"customer_coupon_id":coupon.customer_coupon_id,"cart":cart,"modality":"dine_in"})
    assert selected.status_code==200, selected.text
    body=selected.json()
    assert body["calculation_status"]=="rules_pending" and body["reason"]=="commercial_compatibility_pending"
    assert body["benefit_amount"] is None and body["summary"]["discount_total"]=="9.95"
    with b.sessions() as db:
        assert db.get(CustomerCoupon,coupon.customer_coupon_id).status=="available"


def test_compatible_coupon_summary_and_order_work_with_existing_restricted_writer(restricted_backend):
    b = restricted_backend
    commercial = add_discount(b.sessions,[b.product_id])
    personal = add_discount(b.sessions,[],kind="FIXED_AMOUNT",value="10")
    with b.sessions.begin() as db:
        db.get(CommercialDiscountAssignment,(commercial.discount_id,b.product_id)).conditions = {
            "include_complements":False,"compatible_discount_ids":[personal.discount_id]}
        coupon = CustomerCoupon(account_id=b.ids["customer"][0],name="Cupón sintético integrado",discount_id=personal.discount_id,
            starts_at=b.now.value-timedelta(days=1),expires_at=b.now.value+timedelta(days=1),
            conditions={"scope":"order","compatible_discount_ids":[commercial.discount_id]})
        db.add(coupon)
        db.get(RestaurantOrderSettings,1).accepting_orders = True
    headers = csrf_headers(login(b,"customer"))
    cart = {"items":[{"product_id":b.product_id,"product_variant_id":b.variant_id,"quantity":1}]}
    summary = b.client.post("/api/cart/summary",headers=headers,
        json=cart | {"customer_coupon_id":coupon.customer_coupon_id,"modality":"dine_in"})
    assert summary.status_code == 200 and summary.json()["total"] == "79.55", summary.text
    created = submit(b.client,{"cart":cart,"data":{"modality":"dine_in","name":"Cliente"},
        "customer_coupon_id":coupon.customer_coupon_id},headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["coupon"] == summary.json()["coupon"] and created.json()["total"] == "79.55"
    with b.sessions() as db:
        snapshot = db.get(OrderDiscountSnapshot,UUID(created.json()["order_id"]))
        assert snapshot.calculation["coupon"]["selected_coupon_id"] == coupon.customer_coupon_id
        assert snapshot.calculation["coupon"]["benefit_amount"] == "10.00"
        assert db.get(CustomerCoupon,coupon.customer_coupon_id).status == "available"


def test_staff_order_history_uses_existing_writer_without_private_access_for_readers_or_workers(restricted_backend, compatible_database):
    b = restricted_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db),"20261121_0014")
    execute_script(b.roles.owner,approved(DELTA))
    try:
        with b.sessions.begin() as db:
            db.get(RestaurantOrderSettings,1).accepting_orders = True
        receipt = submit(b.client,{"cart":{"items":[{"product_id":b.product_id,"product_variant_id":b.variant_id,"quantity":1}]},
            "data":{"modality":"dine_in","name":"Invitado"}}).json()
        headers = csrf_headers(login(b,"staff"))
        assert b.client.get("/api/admin/orders").status_code == 200
        accepted = b.client.post(f'/api/admin/orders/{receipt["order_id"]}/accept',json={},headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "Confirmado" and len(accepted.json()["history"]) == 2
        execute_script(b.roles.writer,VERIFY.read_text(encoding="utf-8"))
        with b.roles.writer.connect() as db:
            assert validate_bridge_schema(db) == "20261121_0014"
        for engine in (b.roles.reader,*compatible_database.workers.values()):
            denied(engine,'SELECT * FROM "OrderStatusHistories"')
        for statement in ('UPDATE "OrderStatusHistories" SET status=\'Cancelado\'',
                          'DELETE FROM "OrderStatusHistories"'):
            denied(b.roles.writer,statement)
        for role,filename in (("mitbol_catalog_worker","verify_catalog_worker.sql"),
                              ("mitbol_discount_worker","verify_discount_worker.sql")):
            worker = compatible_database.workers[role]
            execute_script(worker,(SQL/filename).read_text(encoding="utf-8"))
            with worker.connect() as db:
                assert validate_bridge_schema(db) == "20261121_0014"
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db),"20261120_0013")


@pytest.mark.parametrize("revision", ["20261124_0015", "20261124_0016", "20261124_0017", "20261125_0018"])
def test_payment_verification_uses_append_only_writer_without_reader_or_worker_access(restricted_backend, compatible_database, revision):
    """El escritor consulta e inserta pagos; no modifica evidencia ni puede asignar la fecha del servidor."""
    b = restricted_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), revision)
    execute_script(b.roles.owner, approved(DELTA))
    try:
        for engine, filename in ((b.roles.writer, "verify_admin_writer_local.sql"),
                                 (compatible_database.workers["mitbol_catalog_worker"], "verify_catalog_worker.sql"),
                                 (compatible_database.workers["mitbol_discount_worker"], "verify_discount_worker.sql")):
            execute_script(engine, (SQL / filename).read_text(encoding="utf-8"))
            with engine.connect() as db:
                assert validate_bridge_schema(db) == revision
        with b.sessions.begin() as db:
            db.get(RestaurantOrderSettings, 1).accepting_orders = True
        created = submit(b.client, {"cart": {"items": [{"product_id": b.product_id,
            "product_variant_id": b.variant_id, "quantity": 1}]}, "data": {"modality": "dine_in", "name": "Invitado"}})
        assert created.status_code == 201, created.text
        receipt = created.json()
        headers = csrf_headers(login(b, "staff"))
        base = f'/api/admin/orders/{receipt["order_id"]}'
        assert b.client.post(base + "/accept", headers=headers, json={}).status_code == 200
        assert b.client.get(base + "/payment").json()["payment_status"] == "unverified"
        paid = b.client.post(base + "/payment", headers=headers, json={"amount": receipt["total"], "method": "cash"})
        assert paid.status_code == 201, paid.text
        assert b.client.get(base + "/payment").json() == paid.json()
        assert b.client.post(base + "/prepare", headers=headers, json={}).status_code == 200
        assert b.client.get(base + "/payment").json() == paid.json()
        if revision == "20261125_0018":
            from app.models import LoyaltyVisit
            assert b.client.post(base + "/serve", headers=headers, json={}).status_code == 200
            assert b.client.post(base + "/complete", headers=headers, json={}).status_code == 200
            with b.sessions() as db:
                assert db.scalars(select(LoyaltyVisit)).all() == []
        for statement in ('UPDATE "OrderPayments" SET method=\'other\'', 'DELETE FROM "OrderPayments"',
                          'INSERT INTO "OrderPayments" (verified_at) VALUES (now())'):
            denied(b.roles.writer, statement)
        for engine in (b.roles.reader, *compatible_database.workers.values()):
            with engine.connect() as db:
                assert db.exec_driver_sql("""SELECT has_any_column_privilege(current_user, 'public."OrderPayments"',
                    'SELECT,INSERT,UPDATE,REFERENCES'), has_table_privilege(current_user, 'public."OrderPayments"',
                    'DELETE,TRUNCATE,TRIGGER')""").one() == (False, False)
            for statement in ('SELECT * FROM "OrderPayments"',
                              'INSERT INTO "OrderPayments" DEFAULT VALUES',
                              'UPDATE "OrderPayments" SET method=\'other\'',
                              'DELETE FROM "OrderPayments"'):
                code = "25006" if engine is b.roles.reader and not statement.startswith("SELECT") else "42501"
                denied(engine, statement, code=code)
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db), "20261120_0013")


@pytest.mark.parametrize("revision", ["20261124_0016", "20261124_0017", "20261125_0018"])
def test_loyalty_configuration_uses_writer_without_reader_or_worker_access(restricted_backend, compatible_database, revision):
    """Admin configura con permisos por columna; no se concede DELETE ni acceso público o de workers."""
    b = restricted_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), revision)
    execute_script(b.roles.owner, approved(DELTA))
    try:
        execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
        discount = add_discount(b.sessions, [b.product_id], kind="FIXED_AMOUNT", value="10")
        headers = csrf_headers(login(b, "admin"))
        path = "/api/admin/loyalty/goals"
        payload = {"required_visits": 5, "level_name": "Nivel sintético", "reward_name": "Recompensa sintética",
                   "discount_id": discount.discount_id, "valid_days": 30, "conditions": {"scope": "order"}, "is_active": True}
        response = b.client.post(path, headers=headers, json=payload)
        assert response.status_code == 201, response.text
        target = path + "/" + response.json()["goal_id"]
        edited = b.client.put(target, headers=headers, json=payload | {"version": 1, "required_visits": 7})
        assert edited.status_code == 200 and edited.json()["version"] == 2, edited.text
        assert b.client.get(path).json()["total"] == 1
        disabled = b.client.post(target + "/deactivate", headers=headers, json={"version": 2})
        assert disabled.status_code == 200 and disabled.json()["is_active"] is False
        for statement in ('DELETE FROM "LoyaltyGoals"', 'UPDATE "LoyaltyGoals" SET goal_id=goal_id',
                          'UPDATE "LoyaltyGoals" SET created_at=now()', 'INSERT INTO "LoyaltyGoals" (created_at) VALUES (now())'):
            denied(b.roles.writer, statement)
        for engine in (b.roles.reader, *compatible_database.workers.values()):
            with engine.connect() as db:
                if engine is not b.roles.reader:
                    assert validate_bridge_schema(db) == revision
                assert db.exec_driver_sql("""SELECT has_any_column_privilege(current_user, 'public."LoyaltyGoals"',
                    'SELECT,INSERT,UPDATE,REFERENCES'), has_table_privilege(current_user, 'public."LoyaltyGoals"',
                    'DELETE,TRUNCATE,TRIGGER')""").one() == (False, False)
            for statement in ('SELECT * FROM "LoyaltyGoals"', 'INSERT INTO "LoyaltyGoals" DEFAULT VALUES',
                              'UPDATE "LoyaltyGoals" SET is_active=true', 'DELETE FROM "LoyaltyGoals"'):
                code = "25006" if engine is b.roles.reader and not statement.startswith("SELECT") else "42501"
                denied(engine, statement, code=code)
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db), "20261120_0013")


@pytest.mark.parametrize("revision", ["20261124_0017", "20261125_0018"])
def test_loyalty_identification_uses_writer_without_public_or_worker_access(restricted_backend, compatible_database, revision):
    """El pool web emite y valida credenciales sin abrirlas a lectores ni workers."""
    b = restricted_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), revision)
    execute_script(b.roles.owner, approved(DELTA))
    try:
        execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
        headers = csrf_headers(login(b, "customer"))
        issued = b.client.post("/api/auth/loyalty/identification", headers=headers, json={})
        assert issued.status_code == 201, issued.text
        replacement = b.client.post("/api/auth/loyalty/identification", headers=headers, json={})
        assert replacement.status_code == 201 and replacement.json()["qr_payload"] != issued.json()["qr_payload"]
        headers = csrf_headers(login(b, "staff"))
        checked = b.client.post("/api/admin/loyalty/identifications/validate", headers=headers,
                               json={"qr_payload": replacement.json()["qr_payload"]})
        assert checked.status_code == 200, checked.text
        from app.services.loyalty_identification_service import consume_identification
        from app.schemas.loyalty_identification import LoyaltyIdentificationInput
        with b.service.sessions.begin() as db:
            consume_identification(db, LoyaltyIdentificationInput(qr_payload=replacement.json()["qr_payload"]),
                                   actor_account_id=b.ids["staff"][0], clock=lambda: b.now.value)
        assert b.client.post("/api/admin/loyalty/identifications/validate", headers=headers,
            json={"qr_payload": replacement.json()["qr_payload"]}).status_code == 409
        for statement in ('DELETE FROM "LoyaltyIdentifications"',
                          'UPDATE "LoyaltyIdentifications" SET account_id=account_id',
                          'INSERT INTO "LoyaltyIdentifications" (consumed_at) VALUES (now())'):
            denied(b.roles.writer, statement)
        for engine in (b.roles.writer, *compatible_database.workers.values()):
            with engine.connect() as db:
                assert validate_bridge_schema(db) == revision
        for engine in (b.roles.reader, *compatible_database.workers.values()):
            with engine.connect() as db:
                assert db.exec_driver_sql("""SELECT has_any_column_privilege(current_user, 'public."LoyaltyIdentifications"',
                    'SELECT,INSERT,UPDATE,REFERENCES'), has_table_privilege(current_user, 'public."LoyaltyIdentifications"',
                    'DELETE,TRUNCATE,TRIGGER')""").one() == (False, False)
            for statement in ('SELECT * FROM "LoyaltyIdentifications"', 'INSERT INTO "LoyaltyIdentifications" DEFAULT VALUES',
                              'UPDATE "LoyaltyIdentifications" SET consumed_at=now()', 'DELETE FROM "LoyaltyIdentifications"'):
                code = "25006" if engine is b.roles.reader and not statement.startswith("SELECT") else "42501"
                denied(engine, statement, code=code)
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db), "20261120_0013")


@pytest.mark.parametrize("revision", ["20261125_0018", "20261125_0019"])
def test_visit_registration_uses_append_only_writer_without_reader_or_worker_access(restricted_backend, compatible_database, revision):
    """El escritor registra QR y visita en conjunto; no puede editar ni borrar la evidencia."""
    from datetime import datetime, timezone
    from app.services.visit_calendar_service import visit_day

    b = restricted_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), revision)
    execute_script(b.roles.owner, approved(DELTA))
    try:
        execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
        with b.service.sessions.begin() as db:
            assert visit_day(db, datetime(2026, 11, 25, 5, tzinfo=timezone.utc)).isoformat() == "2026-11-24"
        owner_headers = csrf_headers(login(b, "customer"))
        cookies = dict(b.client.cookies)
        issued = b.client.post("/api/auth/loyalty/identification", headers=owner_headers, json={})
        assert issued.status_code == 201, issued.text
        headers = csrf_headers(login(b, "staff"))
        response = b.client.post("/api/admin/loyalty/visits", headers=headers, json={"qr_payload": issued.json()["qr_payload"]})
        assert response.status_code == 201, response.text
        assert "identification_hash" not in response.text
        assert b.client.post("/api/admin/loyalty/visits", headers=headers,
            json={"qr_payload": issued.json()["qr_payload"]}).status_code == 409
        b.client.cookies.clear()
        b.client.cookies.update(cookies)
        renewed = b.client.post("/api/auth/loyalty/identification", headers=owner_headers, json={})
        assert renewed.status_code == 201
        headers = csrf_headers(login(b, "staff"))
        assert b.client.post("/api/admin/loyalty/visits", headers=headers,
            json={"qr_payload": renewed.json()["qr_payload"]}).status_code == 409
        assert b.client.post("/api/admin/loyalty/identifications/validate", headers=headers,
            json={"qr_payload": renewed.json()["qr_payload"]}).status_code == 200
        for statement in ('UPDATE "LoyaltyVisits" SET visited_on=current_date', 'DELETE FROM "LoyaltyVisits"'):
            denied(b.roles.writer, statement)
        for engine in (b.roles.writer, *compatible_database.workers.values()):
            with engine.connect() as db:
                assert validate_bridge_schema(db) == revision
        if revision == "20261125_0019":
            for engine in (b.roles.writer, b.roles.reader, *compatible_database.workers.values()):
                with engine.connect() as db:
                    assert db.exec_driver_sql("""SELECT has_any_column_privilege(current_user, 'public."LoyaltyRewards"',
                        'SELECT,INSERT,UPDATE,REFERENCES'), has_table_privilege(current_user, 'public."LoyaltyRewards"',
                        'DELETE,TRUNCATE,TRIGGER')""").one() == (False, False)
                denied(engine, 'SELECT * FROM "LoyaltyRewards"')
        for engine in (b.roles.reader, *compatible_database.workers.values()):
            with engine.connect() as db:
                assert db.exec_driver_sql("""SELECT has_any_column_privilege(current_user, 'public."LoyaltyVisits"',
                    'SELECT,INSERT,UPDATE,REFERENCES'), has_table_privilege(current_user, 'public."LoyaltyVisits"',
                    'DELETE,TRUNCATE,TRIGGER')""").one() == (False, False)
            for statement in ('SELECT * FROM "LoyaltyVisits"', 'INSERT INTO "LoyaltyVisits" DEFAULT VALUES',
                              'UPDATE "LoyaltyVisits" SET visited_on=current_date', 'DELETE FROM "LoyaltyVisits"'):
                code = "25006" if engine is b.roles.reader and not statement.startswith("SELECT") else "42501"
                denied(engine, statement, code=code)
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db), "20261120_0013")
