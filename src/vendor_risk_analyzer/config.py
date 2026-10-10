from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Vendor Risk Analyzer"
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str

    zitadel_issuer: str
    zitadel_client_id: str
    zitadel_project_id: str
    zitadel_redirect_uri: str
    zitadel_post_logout_uri: str

    session_secret: str
    session_cookie_secure: bool = True
    api_bearer_enabled: bool = False
    mcp_enabled: bool = False
    mcp_public_url: AnyHttpUrl | None = None

    object_storage_endpoint: str
    object_storage_region: str
    object_storage_access_key_id: str
    object_storage_secret_access_key: str
    object_storage_bucket: str = "vendor-documents"
    object_storage_public_endpoint: str | None = None
    object_storage_addressing_style: Literal["auto", "path", "virtual"] = "auto"

    @model_validator(mode="after")
    def validate_runtime_security(self):
        if not self.session_cookie_secure:
            local_hosts = {"localhost", "127.0.0.1", "::1"}
            urls = (self.zitadel_redirect_uri, self.zitadel_post_logout_uri)
            if self.app_env != "development" or any(
                urlsplit(url).hostname not in local_hosts for url in urls
            ):
                raise ValueError(
                    "Non-secure session cookies require development mode "
                    "and localhost login/logout URLs"
                )
        if self.api_bearer_enabled:
            issuer = urlsplit(self.zitadel_issuer)
            if (
                issuer.scheme != "https"
                or not issuer.hostname
                or issuer.username
                or issuer.password
                or issuer.query
                or issuer.fragment
                or not self.zitadel_project_id.strip()
            ):
                raise ValueError(
                    "Bearer authentication requires an HTTPS issuer and project ID"
                )
        if self.mcp_enabled:
            if not self.api_bearer_enabled or self.mcp_public_url is None:
                raise ValueError(
                    "MCP requires bearer authentication and MCP_PUBLIC_URL"
                )
            url = self.mcp_public_url
            if (
                url.username
                or url.password
                or url.query
                or url.fragment
                or url.path != "/mcp"
                or (
                    url.scheme != "https"
                    and not (
                        self.app_env == "development"
                        and url.host in {"localhost", "127.0.0.1", "[::1]"}
                    )
                )
            ):
                raise ValueError(
                    "MCP_PUBLIC_URL must be the HTTPS /mcp endpoint; "
                    "HTTP loopback is allowed only in development"
                )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
