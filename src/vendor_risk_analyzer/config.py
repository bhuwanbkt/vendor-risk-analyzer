from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Vendor Risk Analyzer"
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str

    zitadel_issuer: str
    zitadel_client_id: str
    zitadel_redirect_uri: str
    zitadel_post_logout_uri: str

    session_secret: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()