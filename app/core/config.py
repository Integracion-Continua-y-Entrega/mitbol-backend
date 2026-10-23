from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    loyverse_access_token: str
    database_url: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()