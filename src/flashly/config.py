from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError
from .masking import register_secret

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ".env"), extra="ignore", case_sensitive=False,
        populate_by_name=True
    )

    mode: Literal["mock", "live"] = Field("mock", validation_alias=AliasChoices('mode', 'INSTAGRAM_MODE'))
    graph_api_version: str = Field("v27.0", validation_alias=AliasChoices('graph_api_version', 'META_GRAPH_API_VERSION'))
    graph_base_url: str = "https://graph.facebook.com"
    meta_app_id: str = "1503417951637249"
    facebook_page_id: str = Field("1481254898400944", validation_alias=AliasChoices('facebook_page_id', 'FB_PAGE_ID'))
    ig_user_id: str = Field("17841423089085521", validation_alias=AliasChoices('ig_user_id', 'INSTAGRAM_USER_ID'))
    ig_username: str = Field("flashly_official", validation_alias=AliasChoices('ig_username', 'INSTAGRAM_USERNAME'))

    meta_app_secret: SecretStr | None = Field(None, validation_alias=AliasChoices('meta_app_secret', 'APP_SECRET', 'META_APP_SECRET'))
    ig_access_token: SecretStr | None = Field(None, validation_alias=AliasChoices('ig_access_token', 'INSTAGRAM_ACCESS_TOKEN'))
    mcp_auth_token: SecretStr | None = Field(None, validation_alias=AliasChoices('mcp_auth_token', 'MCP_API_KEY'))

    http_timeout_seconds: float = 30.0
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 30.0

    fixtures_dir: Path = ROOT / "fixtures"

    # Stage 5: history storage + collector
    database_url: SecretStr | None = None   # postgresql+psycopg://user:pass@host/db (contains a password)
    collect_interval_seconds: int = 3600
    collect_max_media: int = 200
    token_warn_days: str = "14,7,3"
    notify_channel: Literal["log", "telegram", "email"] = "log"  # only 'log' is implemented

    def token(self) -> str:
        if not self.ig_access_token or not self.ig_access_token.get_secret_value():
            raise ConfigError("IG_ACCESS_TOKEN is not set (put it in .env).")
        return self.ig_access_token.get_secret_value()

    def validate_for_live(self) -> None:
        self.token()

    def register_secrets(self) -> None:
        for s in (self.meta_app_secret, self.ig_access_token, self.mcp_auth_token, self.database_url):
            if s:
                register_secret(s.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.register_secrets()
    return s
