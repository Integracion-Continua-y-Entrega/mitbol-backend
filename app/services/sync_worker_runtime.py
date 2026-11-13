"""Recursos del proceso operativo: nunca se construyen desde un cuerpo HTTP."""

from dataclasses import dataclass, field
import os
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, create_database_engine, environment_mode,
    local_database_url, validate_local_connection,
)
from app.services.discount_sync_service import DiscountSyncService
from app.services.loyverse_client import LoyverseAPIClient
from app.services.manual_sync_coordinator import ManualSyncCoordinator
from app.services.sync_service import SyncService
from app.services.sync_worker_client import validate_worker_token
from app.schemas.sync_requests import completed_response
from app.schemas.sync_destination import SyncDestination
from app.services.manual_sync_coordinator import SyncDestinationMismatchError


@dataclass(frozen=True)
class SyncWorkerConfig:
    database_url: str = field(repr=False)
    expected_database: str
    store_id: str
    loyverse_token: SecretStr = field(repr=False)
    worker_token: SecretStr = field(repr=False)
    cloud_target: CloudDatabaseTarget | None = None
    destination: SyncDestination = field(init=False)

    def __post_init__(self):
        url = self.cloud_target.validate_url(self.database_url) if self.cloud_target else local_database_url(self.database_url)
        if url.database != self.expected_database or not url.username or url.username in {"mitbol_admin_writer", "mitbol_render_reader"}:
            raise DatabaseTargetError("El worker requiere una identidad operativa en la base autorizada.")
        if self.cloud_target and "-pooler." in self.cloud_target.expected_host:
            raise DatabaseTargetError("El worker requiere el endpoint directo de PostgreSQL.")
        validate_worker_token(self.worker_token.get_secret_value())
        token = self.loyverse_token.get_secret_value()
        if not token or any(c.isspace() for c in token) or token == self.worker_token.get_secret_value():
            raise ValueError("El worker requiere credenciales independientes y un token Loyverse válido.")
        try:
            store_id = str(UUID(self.store_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("El worker requiere un UUID de tienda válido.") from None
        mode = "cloud" if self.cloud_target is not None else environment_mode()
        destination = SyncDestination.from_url(url, environment=mode, store_id=store_id)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "store_id", store_id)
        object.__setattr__(self, "database_url", url.set(port=destination.port).render_as_string(hide_password=False))
        DiscountSyncService(lambda: None, expected_database=self.expected_database,
                            store_id=self.store_id, cloud_target=self.cloud_target)

    @classmethod
    def from_environment(cls):
        target = None
        if environment_mode() == "cloud":
            target = CloudDatabaseTarget(os.getenv("SYNC_EXPECTED_DATABASE", ""), os.getenv("SYNC_EXPECTED_HOST", ""))
        return cls(database_url=os.getenv("SYNC_DATABASE_URL", ""),
                   expected_database=os.getenv("SYNC_EXPECTED_DATABASE", ""),
                   store_id=os.getenv("LOYVERSE_STORE_ID", ""),
                   loyverse_token=SecretStr(os.getenv("LOYVERSE_ACCESS_TOKEN", "")),
                   worker_token=SecretStr(os.getenv("SYNC_WORKER_TOKEN", "")), cloud_target=target)


class SyncWorkerRuntime:
    def __init__(self, config: SyncWorkerConfig, *, engine=None):
        self.config = config
        self.engine = engine if engine is not None else create_database_engine(config.database_url, target=config.cloud_target)
        sessions = sessionmaker(bind=self.engine)
        self.catalog = SyncService(sessions, expected_database=config.expected_database,
                                   store_id=config.store_id, cloud_target=config.cloud_target)
        self.discounts = DiscountSyncService(sessions, expected_database=config.expected_database,
                                             store_id=config.store_id, cloud_target=config.cloud_target)
        self.coordinator = ManualSyncCoordinator(catalog_runner=self.run_catalog, discounts_runner=self.run_discounts)

    def require_destination(self, expected: SyncDestination):
        if expected != self.config.destination:
            raise SyncDestinationMismatchError("El destino recibido no corresponde a este worker.")

    def _validate_actual_destination(self):
        # Metadatos de la conexión real, sin privilegios extra o escritura.
        with self.engine.connect() as connection:
            info = connection.connection.driver_connection.info
            actual = SyncDestination(environment=self.config.destination.environment,
                host=info.host, port=int(info.port), database=info.dbname, store_id=self.config.store_id)
            self.require_destination(actual)

    def preflight(self, target):
        self._validate_actual_destination()
        if target == "discounts":
            from app.cli.sync_discounts import preflight
            return preflight(self.engine, expected_database=self.config.expected_database, cloud_target=self.config.cloud_target)
        from app.cli.sync_catalog import _inspect_operator
        with self.engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            if int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 != 18:
                raise DatabaseTargetError("El worker requiere PostgreSQL 18.")
            if self.config.cloud_target:
                self.config.cloud_target.validate_connection(connection)
            else:
                validate_local_connection(connection)
            if MigrationContext.configure(connection, opts={"version_table_schema": "public"}).get_current_heads() != ("20261111_0006",):
                raise DatabaseTargetError("El worker requiere la revisión 20261111_0006.")
            _inspect_operator(connection)
        return {"writes": False, "loyverse_contacted": False}

    async def run_catalog(self):
        self.preflight("catalog")
        async with LoyverseAPIClient(access_token=self.config.loyverse_token) as client:
            return await self.catalog.synchronize(client)

    async def run_discounts(self):
        self.preflight("discounts")
        async with LoyverseAPIClient(access_token=self.config.loyverse_token) as client:
            return await self.discounts.synchronize(client)

    async def execute(self, target):
        return completed_response(target, await self.coordinator.execute(target))

    def close(self):
        self.engine.dispose()
