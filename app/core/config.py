from pathlib import Path
import re

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    loyverse_access_token: SecretStr
    loyverse_store_id: str | None = None
    database_url: str
    admin_enabled: bool = False
    admin_api_key: SecretStr | None = None
    image_storage_root: Path = Path("storage/product-images")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_admin_configuration(self):
        if self.admin_enabled:
            key = self.admin_api_key.get_secret_value() if self.admin_api_key else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
                raise ValueError("La administración local requiere una clave aleatoria de 32 a 128 caracteres URL-safe.")
            if key == self.loyverse_access_token.get_secret_value():
                raise ValueError("La credencial administrativa debe ser independiente de Loyverse.")
        return self

settings = Settings()
