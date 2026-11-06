from pathlib import Path
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.connection import CloudDatabaseTarget, environment_mode, local_database_url, postgresql_url


class Settings(BaseSettings):
    app_env: str = "local"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    loyverse_access_token: SecretStr | None = None
    loyverse_store_id: str | None = None
    database_url: str = Field(repr=False)
    expected_database: str | None = None
    expected_database_host: str | None = None
    admin_enabled: bool = False
    admin_api_key: SecretStr | None = None
    image_storage_root: Path = Path("storage/product-images")
    auth_enabled: bool = False
    auth_catalog_enabled: bool = False
    auth_images_enabled: bool = False
    auth_database_url: str | None = Field(default=None, repr=False)
    auth_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    auth_cookie_secure: bool = True
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

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True,
    )

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
        postgresql_url(self.database_url)
        if self.cloud_target is not None:
            self.cloud_target.validate_url(self.database_url)
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
            if self.auth_enabled:
                raise ValueError("AUTH_ENABLED está bloqueado en cloud hasta aprobar dominios, proxy y publicación; un limitador no basta.")
            if not self.auth_cookie_secure:
                raise ValueError("Cloud exige cookies Secure; HTTP sólo se admite en desarrollo local.")
        if not self.auth_enabled:
            return self
        if self.auth_images_enabled and not self.auth_catalog_enabled:
            raise ValueError("Las imágenes requieren habilitar explícitamente el catálogo administrativo.")
        if self.admin_enabled:
            raise ValueError("La autenticación de cuentas y la clave administrativa local son modos excluyentes.")
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
        url = local_database_url(self.auth_database_url)
        if (not re.fullmatch(r"mitbol_test_auth_[a-f0-9]{32}|mitbol_auth_local", url.database or "")
                or not url.username or url.username in {"neondb_owner", "mitbol_render_reader"}
                or set(url.query) - {"sslmode", "connect_timeout", "application_name"}):
            raise ValueError("La autenticación local requiere una base aislada y su identidad técnica local.")
        self.explicit_origins(self.auth_origins)
        if not self.auth_origins or any(urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}
                                        for origin in self.auth_origins):
            raise ValueError("AUTH_ORIGINS sólo admite orígenes loopback explícitos en esta fase.")
        return self

settings = Settings()
