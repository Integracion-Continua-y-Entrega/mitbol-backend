"""Ciclo completo con commits reales en la base temporal vacía del ejecutor."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from test_discount_migration import staged_config
from app.services.discount_sync_service import DiscountSyncService
from app.services.discount_transform import DiscountValidationError
from app.models import Base, AuthBase


STORE = "a3a56a06-d40c-4c07-931c-41abcbaaba12"
IDENTIFIER = "497f6eca-6276-4993-bfeb-53cbbbba6f08"


@pytest.fixture
def database(empty_postgresql_database, staged_config):
    engine = empty_postgresql_database
    assert engine.url.database.startswith("mitbol_test_hu008_")
    with engine.begin() as connection:
        staged_config.attributes["connection"] = connection
        command.upgrade(staged_config, "20261111_0006")
        context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, [Base.metadata, AuthBase.metadata]) == []
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            staged_config.attributes["connection"] = connection
            command.downgrade(staged_config, "base")
            # Alembic conserva su tabla vacía al llegar a base; la fixture exige
            # no dejar objetos en ESTA base temporal al terminar.
            connection.exec_driver_sql("DROP TABLE public.alembic_version")


def source(**changes):
    values = dict(id=IDENTIFIER, name="Importación", type="FIXED_AMOUNT",
                  discount_amount=Decimal("35.25"), discount_percent=None,
                  stores=[STORE], restricted_access=False,
                  created_at="2026-11-01T12:00:00Z", updated_at="2026-11-12T12:00:00Z", deleted_at=None)
    values.update(changes)
    return values


def stored(engine):
    with engine.connect() as db:
        return dict(db.execute(text('SELECT * FROM public."Discounts"')).mappings().one())


def test_committed_lifecycle_preserves_identity_and_data_on_bad_response(database):
    service = DiscountSyncService(sessionmaker(bind=database), expected_database=database.url.database, store_id=STORE)
    client = Mock(get_discounts=AsyncMock(return_value=[source()]))
    assert asyncio.run(service.synchronize(client)) == 1
    original = stored(database)  # Otra conexión comprueba el commit real.
    assert original["value"] == Decimal("35.25")
    assert asyncio.run(service.synchronize(client)) == 1
    assert stored(database)["discount_id"] == original["discount_id"]

    client.get_discounts.return_value = [source(type="VARIABLE_AMOUNT", discount_amount=None)]
    assert asyncio.run(service.synchronize(client)) == 1
    variable = stored(database)
    assert variable["value"] is None
    assert variable["created_at"] == original["created_at"]
    client.get_discounts.return_value = [source(deleted_at="2026-11-12T13:00:00Z")]
    asyncio.run(service.synchronize(client))
    deleted = stored(database)
    assert deleted["is_active"] is False

    client.get_discounts.return_value = [source(name="No guardar"), {"id": "invalid"}]
    with pytest.raises(DiscountValidationError):
        asyncio.run(service.synchronize(client))
    assert stored(database) == deleted
    client.get_discounts.return_value = []
    assert asyncio.run(service.synchronize(client)) == 0
    assert stored(database) == deleted


def test_cli_preflight_then_execute_with_fake_provider_and_real_commit(database, monkeypatch, capsys):
    from app.cli import sync_discounts as cli
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("DATABASE_URL", database.url.render_as_string(hide_password=False))
    monkeypatch.setenv("LOYVERSE_STORE_ID", STORE)
    monkeypatch.delenv("LOYVERSE_ACCESS_TOKEN", raising=False)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get_discounts.return_value = [source()]
    factory = Mock(return_value=client)
    monkeypatch.setattr(cli, "LoyverseAPIClient", factory)
    args = ["--expected-database", database.url.database, "--store-id", STORE]
    assert cli.main(args) == 0
    factory.assert_not_called()
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "synthetic-token")
    assert cli.main(args + ["--execute"]) == 0
    assert stored(database)["value"] == Decimal("35.25")
    client.get_discounts.assert_awaited_once_with(include_deleted=True)
    assert "synthetic-token" not in capsys.readouterr().out
