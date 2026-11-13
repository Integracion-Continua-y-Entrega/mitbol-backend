from pathlib import Path
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.connection import CloudDatabaseTarget, environment_mode, local_database_url, postgresql_url


class Settings(BaseSettings):
    app_env: str = "local"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    public_menu_url: str | None = None
    loyverse_access_token: SecretStr | None = None
    loyverse_store_id: str | None = None
    database_url: str = Field(repr=False)
    expected_database: str | None = None
    expected_database_host: str | None = None
    admin_enabled: bool = False
    admin_api_key: SecretStr | None = None
    image_storage_root: Path = Path("storage/product-images")
    auth_enabled: bool = False
    auth_profile: Literal["restaurant", "frontend_dev"] = "restaurant"
    auth_catalog_enabled: bool = False
    auth_images_enabled: bool = False
    auth_database_url: str | None = Field(default=None, repr=False)
    auth_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    auth_cookie_secure: bool = True
    auth_cookie_partitioned: bool = False
    auth_public_origin: str | None = None
    auth_proxy_mode: Literal["direct", "render"] = "direct"
    auth_absolute_seconds: int = Field(default=28_800, gt=0, le=28_800)
    auth_idle_seconds: int = Field(default=1_800, gt=0, le=28_800)
    auth_touch_seconds: int = Field(default=60, ge=0, le=300)
    auth_login_attempts: int = Field(default=5, ge=1, le=100)
    auth_ip_attempts: int = Field(default=30, ge=1, le=1000)
    auth_limit_window_seconds: int = Field(default=900, ge=1, le=3600)
    auth_limit_max_keys: int = Field(default=2000, ge=2, le=100_000)
    auth_hash_concurrency: int = Field(default=2, ge=1, le=4)
    auth_limit_backend: Literal["memory", "postgresql"] = "memory"
    auth_limit_secret: SecretStr | None = None
    sync_worker_url: str | None = None
    sync_worker_token: SecretStr | None = None
    sync_worker_timeout_seconds: int = Field(default=120, ge=1, le=300)

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_sync_worker(self):
        if (self.sync_worker_url is None) != (self.sync_worker_token is None):
            raise ValueError("SYNC_WORKER_URL y SYNC_WORKER_TOKEN deben configurarse juntos.")
        if self.sync_worker_url is not None:
            from app.services.sync_worker_client import validate_worker_token, validate_worker_url
            self.sync_worker_url = validate_worker_url(self.sync_worker_url)
            validate_worker_token(self.sync_worker_token.get_secret_value())
            # Credenciales distintas sí; catálogos distintos no. Fijar el puerto
            # evita que defaults libpq diferentes desvíen uno de los procesos.
            destination = self.sync_destination
            self.database_url = postgresql_url(self.database_url).set(port=destination.port).render_as_string(hide_password=False)
            self.auth_database_url = postgresql_url(self.auth_database_url).set(port=destination.port).render_as_string(hide_password=False)
        return self

    @property
    def sync_destination(self):
        from app.schemas.sync_destination import SyncDestination
        if not self.auth_database_url or not self.loyverse_store_id:
            raise ValueError("La delegación requiere AUTH_DATABASE_URL y LOYVERSE_STORE_ID explícitos.")
        public = SyncDestination.from_url(self.database_url, environment=self.app_env, store_id=self.loyverse_store_id)
        admin = SyncDestination.from_url(self.auth_database_url, environment=self.app_env, store_id=self.loyverse_store_id)
        if public != admin:
            raise ValueError("El menú y la administración deben consultar el mismo destino de catálogo para sincronizar.")
        return public

    def __init__(self, **values):
        mode = values.get("app_env", os.getenv("APP_ENV", "local"))
        if str(mode).strip().lower() in {"cloud", "test"} or os.getenv("RENDER", "").lower() == "true":
            # Pydantic construye y lee DotEnvSettingsSource antes de llamar a
            # settings_customise_sources: excluirla allí no evita abrir .env.
            values["_env_file"] = None
        super().__init__(**values)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        mode = init_settings.init_kwargs.get("app_env", os.getenv("APP_ENV", "local"))
        if str(mode).strip().lower() in {"cloud", "test"} or os.getenv("RENDER", "").lower() == "true":
            # Ni dotenv ni secretos de archivo completan variables cloud ausentes.
            return init_settings, env_settings

        def local_dotenv():
            values = dotenv_settings()
            if str(values.get("app_env", "local")).strip().lower() == "cloud":
                raise ValueError("APP_ENV=cloud debe configurarse en el proceso, no en .env.")
            return values

        return init_settings, env_settings, local_dotenv, file_secret_settings

    @field_validator("app_env")
    @classmethod
    def valid_environment(cls, value):
        return environment_mode(value)

    @field_validator("public_menu_url")
    @classmethod
    def valid_public_menu_url(cls, value):
        if value is None or value == "":
            return None
        message = "PUBLIC_MENU_URL requiere una URL HTTP(S) sin credenciales, query ni fragmento; HTTP sólo en loopback."
        if value != value.strip() or "\\" in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(message)
        try:
            url = HttpUrl(value)
        except ValidationError:
            raise ValueError(message) from None
        if (url.username is not None or url.password is not None
                or "@" in urlsplit(value).netloc or url.query is not None or url.fragment is not None
                or (url.scheme == "http" and urlsplit(str(url)).hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError(message)
        return str(url)

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, values):
        for value in values:
            try:
                url = urlsplit(value)
                valid = (url.scheme in {"http", "https"} and bool(url.hostname)
                         and value == f"{url.scheme}://{url.netloc}" and "*" not in value
                         and not url.username and not url.password
                         and (url.port is None or 1 <= url.port <= 65535))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("CORS_ORIGINS requiere orígenes HTTP(S) explícitos, sin rutas ni credenciales.")
        return list(dict.fromkeys(values))

    @property
    def cloud_target(self) -> CloudDatabaseTarget | None:
        if self.app_env != "cloud":
            return None
        return CloudDatabaseTarget(self.expected_database or "", self.expected_database_host or "")

    @model_validator(mode="after")
    def validate_database_configuration(self):
        environment_mode(self.app_env)
        if self.auth_profile == "frontend_dev" and self.app_env != "cloud":
            raise ValueError("frontend_dev requiere APP_ENV=cloud para el entorno académico Render.")
        postgresql_url(self.database_url)
        if self.cloud_target is not None:
            self.cloud_target.validate_url(self.database_url)
            if self.auth_profile == "frontend_dev":
                # Perfil opt-in del entorno académico actual, también al preparar
                # variables con AUTH_ENABLED=false. No deducir uso por nombre de rama.
                if (os.getenv("RENDER") != "true"
                        or self.auth_public_origin != "https://mitbol-backend.onrender.com"
                        or os.getenv("RENDER_EXTERNAL_URL") != self.auth_public_origin):
                    raise ValueError("frontend_dev requiere el Render académico actual y AUTH_PUBLIC_ORIGIN igual a RENDER_EXTERNAL_URL.")
            if self.public_menu_url and (urlsplit(self.public_menu_url).scheme != "https"
                    or urlsplit(self.public_menu_url).hostname in {"localhost", "127.0.0.1", "::1"}):
                raise ValueError("PUBLIC_MENU_URL cloud requiere un destino HTTPS remoto.")
            if "cors_origins" not in self.model_fields_set or not self.cors_origins:
                raise ValueError("Cloud requiere CORS_ORIGINS explícito y no vacío.")
            for origin in self.cors_origins:
                url = urlsplit(origin)
                if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    raise ValueError("Los orígenes remotos cloud deben utilizar HTTPS.")
        return self

    @model_validator(mode="after")
    def validate_admin_configuration(self):
        if self.app_env == "cloud" and self.admin_enabled:
            raise ValueError("Cloud requiere ADMIN_ENABLED=false; la clave local no autoriza acceso remoto.")
        if self.admin_enabled:
            key = self.admin_api_key.get_secret_value() if self.admin_api_key else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
                raise ValueError("La administración local requiere una clave aleatoria de 32 a 128 caracteres URL-safe.")
            if self.loyverse_access_token and key == self.loyverse_access_token.get_secret_value():
                raise ValueError("La credencial administrativa debe ser independiente de Loyverse.")
        return self

    @model_validator(mode="after")
    def validate_auth_configuration(self):
        if self.app_env == "cloud":
            if not self.auth_cookie_secure:
                raise ValueError("Cloud exige cookies Secure; HTTP sólo se admite en desarrollo local.")
            if self.auth_enabled and (os.getenv("RENDER") != "true" or self.auth_proxy_mode != "render"
                    or not self.auth_cookie_partitioned or self.auth_limit_backend != "postgresql"
                    or self.expected_database != "mitbol_cloud_dev"):
                raise ValueError("AUTH_ENABLED bloqueado: cloud requiere el perfil Render explícito, cookies particionadas, PostgreSQL y orígenes aprobados.")
            if self.auth_enabled:
                if self.auth_profile == "frontend_dev":
                    self.validate_database_configuration()
                    if self.auth_origins != ["http://localhost:5173"]:
                        raise ValueError("frontend_dev sólo admite el origen http://localhost:5173.")
                elif (self.auth_public_origin != "https://mitbol-backend.onrender.com"
                        or self.auth_origins != ["https://mitbol-frontend.onrender.com"] or self.auth_images_enabled):
                    raise ValueError("AUTH_ENABLED bloqueado: el perfil del restaurante no admite localhost ni escrituras de imágenes.")
        if not self.auth_enabled:
            return self
        if self.auth_images_enabled and not self.auth_catalog_enabled:
            raise ValueError("Las imágenes requieren habilitar explícitamente el catálogo administrativo.")
        if self.admin_enabled:
            raise ValueError("La autenticación de cuentas y la clave administrativa local son modos excluyentes.")
        if self.auth_cookie_partitioned and not self.auth_cookie_secure:
            raise ValueError("Las cookies Partitioned requieren Secure.")
        if self.auth_idle_seconds > self.auth_absolute_seconds or self.auth_touch_seconds >= self.auth_idle_seconds:
            raise ValueError("Los plazos de sesión y renovación no son coherentes.")
        if self.auth_limit_backend == "postgresql":
            from app.core.session_tokens import hash_session_token
            try:
                hash_session_token(self.auth_limit_secret.get_secret_value() if self.auth_limit_secret else None)
            except ValueError:
                raise ValueError("AUTH_LIMIT_SECRET requiere un secreto URL-safe canónico de 32 bytes, compartido por las instancias.") from None
        if self.auth_limit_backend == "memory" and any(os.getenv(name, "1") != "1" for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS")):
            raise ValueError("El limitador local sólo admite un proceso/worker.")
        if not self.auth_database_url:
            raise ValueError("AUTH_DATABASE_URL explícita es obligatoria para el módulo local.")
        self.explicit_origins(self.auth_origins)
        if self.app_env == "cloud":
            # Perfil cerrado para la topología actual. No confiar en X-Forwarded-For:
            # la cuota de red se comparte globalmente entre las instancias.
            url = self.cloud_target.validate_url(self.auth_database_url)
            public_url = self.cloud_target.validate_url(self.database_url)
            if url.username != "mitbol_admin_writer" or public_url.username != "mitbol_render_reader":
                raise ValueError("Cloud requiere writer administrativo y reader público independientes.")
            return self
        url = local_database_url(self.auth_database_url)
        if (not re.fullmatch(r"mitbol_test_auth_[a-f0-9]{32}|mitbol_auth_local", url.database or "")
                or not url.username or url.username in {"neondb_owner", "mitbol_render_reader"}
                or set(url.query) - {"sslmode", "connect_timeout", "application_name"}):
            raise ValueError("La autenticación local requiere una base aislada y su identidad técnica local.")
        if not self.auth_origins or any(urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}
                                        for origin in self.auth_origins):
            raise ValueError("AUTH_ORIGINS sólo admite orígenes loopback explícitos en esta fase.")
        return self

settings = Settings()
