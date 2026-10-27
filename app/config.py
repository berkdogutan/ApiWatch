"""Application settings loaded from environment variables (prefix ``APIWATCH_``) or ``.env``."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APIWATCH_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/apiwatch.db"

    # Development server binding. Keep 127.0.0.1: APIWatch has no authentication.
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Host header values the dashboard/API answer to. Rejecting other names blocks
    # DNS-rebinding attacks against the local server from malicious web pages.
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost"]
    )

    # Outbound request safety ------------------------------------------------
    # DEVELOPMENT ONLY: permit loopback targets (localhost / 127.0.0.0/8 / ::1) so the
    # local demo mock API can be monitored. Private networks and cloud metadata
    # addresses stay blocked even when this is enabled.
    allow_localhost: bool = False
    # Optional allowlist of monitor target hosts ("api.example.com" or "*.example.com").
    # Empty means "any public host". Allowlisted hosts must still resolve to public addresses.
    allowed_target_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_response_bytes: int = Field(default=1_048_576, ge=1024)

    # Scheduling --------------------------------------------------------------
    scheduler_enabled: bool = True

    # Incidents ---------------------------------------------------------------
    incident_failure_threshold: int = Field(default=2, ge=1)

    @field_validator("trusted_hosts", "allowed_target_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
