"""Laboratorio React sobre el clúster exclusivo de scripts/test_auth_phase1.py.

No admite DSN, destino o contraseñas del usuario. Credenciales sintéticas públicas,
servicio sólo en loopback y esquema/roles únicamente en el PostgreSQL desechable.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import sys
import tempfile
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[2]
DEMO_PASSWORD = "Solo laboratorio MITBOL 2026!"


def temporary_target():
    if os.getenv("APP_ENV") != "test" or os.getenv("RENDER"):
        raise ValueError("El laboratorio requiere el ejecutor temporal local.")
    if any(os.getenv(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSWORD", "PGPASSFILE")):
        raise ValueError("El laboratorio rechaza variables libpq heredadas.")
    directory = Path(os.environ["MITBOL_AUTH_TEST_CLUSTER"]).resolve()
    port = int(os.environ["MITBOL_AUTH_TEST_PORT"])
    if (directory.name != "data" or directory.parent.parent != Path(tempfile.gettempdir()).resolve()
            or not re.fullmatch(r"mitbol-sync-preflight-[a-f0-9]{32}", directory.parent.name)
            or not 1024 <= port <= 65535):
        raise ValueError("Directorio o puerto temporal no autorizados.")
    return directory, URL.create("postgresql+psycopg2", username="mitbol_ci", host="127.0.0.1", port=port,
                                 database="postgres")


def build_demo():
    directory, control_url = temporary_target()
    control = create_engine(control_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    name = "mitbol_test_auth_" + uuid4().hex
    try:
        with control.connect() as db:
            actual = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int, "
                                     "current_setting('data_directory'),host(inet_server_addr())")).one()
            if (actual[0:2] != ("mitbol_ci", "mitbol_ci") or actual[2] // 10000 != 18
                    or Path(actual[3]).resolve() != directory or actual[4] != "127.0.0.1"):
                raise ValueError("El servidor no es el clúster temporal del ejecutor.")
            db.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
    finally:
        control.dispose()
    owner = create_engine(control_url.set(database=name), hide_parameters=True)
    try:
        config = Config(str(ROOT / "alembic.ini"))
        with owner.begin() as db:
            config.attributes["connection"] = db
            command.upgrade(config, "20261106_0005")
        # Importar sólo después de verificar destino; nunca Settings/.env local.
        from app.models import Product, ProductVariant
        from app.services.account_provisioning import AccountData, create_account
        with sessionmaker(bind=owner).begin() as db:
            for role in ("admin", "staff"):
                create_account(db, AccountData(name=f"Demo {role}", phone="0000000000",
                    email=f"{role}@mitbol.example.test", employee_code=f"DEMO-{role}",
                    hired_at=date(2026, 1, 1), active=True, roles=(role,)), DEMO_PASSWORD)
            product = Product(name="Hamburguesa demo", loyverse_id="synthetic-product", description="Datos efímeros",
                              is_active=True, is_available=True, synced_at=datetime.now(timezone.utc))
            product.variants = [ProductVariant(name="Sencilla", loyverse_id="synthetic-variant", price=Decimal("89.00"),
                is_available=True, is_commercially_available=True, synced_at=datetime.now(timezone.utc))]
            db.add(product)
        reader_sql = (ROOT / "docs/database/prepare_render_reader.sql").read_text(encoding="utf-8")
        reader_sql = reader_sql.replace("mitbol_cloud_dev", name).replace("neondb_owner", "mitbol_ci").replace("20261102_0003", "20261106_0005")
        for sql in (reader_sql, (ROOT / "docs/database/prepare_admin_writer_local.sql").read_text(encoding="utf-8")):
            with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
                try:
                    db.exec_driver_sql(sql, execution_options={"no_parameters": True})
                except Exception:
                    db.exec_driver_sql("ROLLBACK")
                    raise
    finally:
        owner.dispose()
    from app.core.config import Settings
    from app.main import create_app
    settings = Settings(_env_file=None, app_env="test", admin_enabled=False,
        database_url=control_url.set(database=name, username="mitbol_render_reader").render_as_string(hide_password=False),
        auth_database_url=control_url.set(database=name, username="mitbol_admin_writer").render_as_string(hide_password=False),
        auth_enabled=True, auth_catalog_enabled=True, auth_images_enabled=True, auth_cookie_secure=False,
        auth_origins=["http://localhost:5173"], cors_origins=["http://localhost:5173"],
        auth_limit_backend="postgresql", auth_limit_secret=token_urlsafe(32),
        image_storage_root=directory.parent / "demo-images", public_menu_url="http://localhost:5173/")
    return create_app(settings)


def smoke_check(application):
    from fastapi.testclient import TestClient
    from app.models import AuditEvent
    with TestClient(application, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        with application.state.auth_session_factory() as db:
            before = db.scalar(select(func.count(AuditEvent.audit_event_id)))
        product = client.get("/api/menu").json()["products"][0]
        for role in ("admin", "staff"):
            login = client.post("/api/auth/login", headers={"Origin": "http://localhost:5173", "X-CSRF-Protection": "1"},
                json={"email": f"{role}@mitbol.example.test", "password": DEMO_PASSWORD})
            assert login.status_code == 200 and login.json()["user"]["roles"] == [role]
            headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": login.json()["csrf_token"]}
            assert client.patch(f'/api/admin/products/{product["product_id"]}/availability',
                headers=headers, json={"is_available": False}).status_code == 200
            assert client.patch(f'/api/admin/products/{product["product_id"]}/description',
                headers=headers, json={"description": "Demo local"}).status_code == (200 if role == "admin" else 403)
            assert client.post("/api/auth/logout", headers=headers, json={}).status_code == 204
            assert client.get("/api/auth/me").status_code == 401
        with application.state.auth_session_factory() as db:
            assert db.scalar(select(func.count(AuditEvent.audit_event_id))) >= before + 7
    print("ADMIN_DEMO_OK: login Admin/Staff, permisos, escritura auditada y logout; sólo PostgreSQL temporal.")


def main():
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("Usar scripts/test_auth_phase1.py --serve-admin [--check].")
    application = build_demo()
    if sys.argv[1:] == ["--check"]:
        smoke_check(application)
    else:
        import uvicorn
        print("Laboratorio efímero: API http://localhost:8000 /docs; React http://localhost:5173", flush=True)
        print("Usuarios SINTÉTICOS: admin@mitbol.example.test / staff@mitbol.example.test", flush=True)
        print(f"Contraseña pública EXCLUSIVA del laboratorio: {DEMO_PASSWORD}", flush=True)
        print("Ctrl+C detiene servidor y PostgreSQL temporal. No usar estas credenciales en production.", flush=True)
        uvicorn.run(application, host="127.0.0.1", port=8000, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
