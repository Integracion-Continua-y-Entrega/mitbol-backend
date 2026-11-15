"""Inventario no destructivo sobre archivos y PostgreSQL aislados."""

from hashlib import sha256
import pytest
from sqlalchemy import select

from app.cli.inventory_images import inventory_images, main
from app.models import ProductImage, ProductVariant
from app.services.image_storage import LocalImageStorage
from test_enrichment_api import picture


def catalog(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    with sync_db.sessions() as db:
        return list(db.scalars(select(ProductVariant).order_by(ProductVariant.product_variant_id)))


def test_inventory_classifies_registered_files_without_changing_rows_or_bytes(sync_db, commercial_snapshot, tmp_path):
    variant, other, *_ = catalog(sync_db, commercial_snapshot)
    storage = LocalImageStorage(tmp_path / "images")
    data = picture()
    good = storage.prepare_create(data, "png", variant.product_id, variant.product_variant_id)
    corrupt = storage.prepare_create(b"corrupt", "png", variant.product_id, variant.product_variant_id)
    urls = [good.image_url, corrupt.image_url,
            f"/media/products/{variant.product_id}/variants/{variant.product_variant_id}/" + "a" * 32 + ".png",
            f"/media/products/{variant.product_id}/variants/{other.product_variant_id}/" + "b" * 32 + ".png"]
    with sync_db.sessions.begin() as db:
        for order, url in enumerate(urls):
            db.add(ProductImage(product_variant_id=variant.product_variant_id, image_url=url, display_order=order))
    storage.settle(good, True)
    storage.settle(corrupt, True)
    before = {str(path.relative_to(storage.root)): path.read_bytes() for path in storage.root.rglob("*") if path.is_file()}
    with sync_db.sessions() as db:
        report = inventory_images(db, storage.root)
        assert len(list(db.scalars(select(ProductImage)))) == 4
    assert report["dry_run"] and not report["writes"] and not report["r2_contacted"]
    assert [row["status"] for row in report["images"]] == ["recoverable", "invalid_image", "missing", "invalid_reference"]
    assert report["images"][0]["sha256"] == sha256(data).hexdigest()
    assert [row["display_order"] for row in report["images"]] == list(range(4))
    assert {str(path.relative_to(storage.root)): path.read_bytes() for path in storage.root.rglob("*") if path.is_file()} == before


def test_cli_uses_explicit_database_read_only_and_never_loads_dotenv(sync_db, commercial_snapshot, tmp_path, monkeypatch, capsys):
    catalog(sync_db, commercial_snapshot)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", sync_db.engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr("dotenv.dotenv_values", lambda *a, **k: pytest.fail("No se permite .env"))
    statements = []
    from sqlalchemy import event
    from app.db.connection import create_database_engine

    def engine_factory(*args, **kwargs):
        engine = create_database_engine(*args, **kwargs)
        event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, *rest: statements.append(statement))
        return engine
    monkeypatch.setattr("app.cli.inventory_images.create_database_engine", engine_factory)
    assert main(["--local-root", str(tmp_path), "--expected-database", sync_db.engine.url.database, "--dry-run"]) == 0
    assert '"writes": false' in capsys.readouterr().out
    assert "SET TRANSACTION READ ONLY" in statements
    assert all(not sql.lstrip().upper().startswith(("INSERT", "DELETE", "UPDATE", "CREATE", "ALTER")) for sql in statements)


def test_cli_rejects_remote_destinations_in_local_mode_before_connecting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://private:SECRET@remote.example/mitbol_dev")
    monkeypatch.setattr("app.cli.inventory_images.create_database_engine", lambda *a, **k: pytest.fail("Conexión no autorizada"))
    assert main(["--local-root", str(tmp_path), "--expected-database", "mitbol_dev"]) == 1
    assert "SECRET" not in capsys.readouterr().out


def test_cli_has_no_execute_mode(tmp_path):
    with pytest.raises(SystemExit):
        main(["--local-root", str(tmp_path), "--expected-database", "unused", "--execute"])
