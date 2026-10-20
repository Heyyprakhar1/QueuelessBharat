"""Application configuration and environment settings."""

import json
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import quote_plus

from pydantic import BeforeValidator, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_origins(v: Any) -> list[str]:
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            try:
                return json.loads(v)
            except Exception:
                pass
        return [origin.strip() for origin in v.split(",") if origin.strip()]
    elif isinstance(v, list):
        return v
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


class Settings(BaseSettings):
    """Strongly typed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime metadata
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "QueuelessBharat"
    API_V1_STR: str = "/api/v1"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # Security & CORS: explicit origin list, never wildcard in production
    ALLOWED_ORIGINS: Annotated[list[str], BeforeValidator(_parse_origins)] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Authentication & Security
    AUTH_SECRET_KEY: str = "dev-insecure-secret-key-replace-in-production-min-32-chars-long!"
    AUTH_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    ENABLE_HSTS: bool = False

    # Google Identity / OpenID Connect Settings
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_AUTH_ENABLED: bool = False

    # Trusted Proxy & Rate Limiting
    TRUST_PROXY_HEADERS: bool = False
    TRUSTED_PROXIES: Annotated[list[str], BeforeValidator(_parse_origins)] = [
        "127.0.0.1",
        "::1",
    ]
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_LOGIN_MAX: int = 100
    RATE_LIMIT_LOGIN_WINDOW: int = 60
    RATE_LIMIT_REGISTER_MAX: int = 50
    RATE_LIMIT_REGISTER_WINDOW: int = 60
    RATE_LIMIT_CHECKIN_MAX: int = 200
    RATE_LIMIT_CHECKIN_WINDOW: int = 60
    RATE_LIMIT_POLL_SERVICE_MAX: int = 120
    RATE_LIMIT_POLL_SERVICE_WINDOW: int = 60
    RATE_LIMIT_POLL_SESSION_MAX: int = 60
    RATE_LIMIT_POLL_SESSION_WINDOW: int = 60
    RATE_LIMIT_VISIT_PLAN_MAX: int = 120
    RATE_LIMIT_VISIT_PLAN_WINDOW: int = 60
    RATE_LIMIT_OBSERVATION_MAX: int = 30
    RATE_LIMIT_OBSERVATION_WINDOW: int = 60

    # Request Bounds
    MAX_REQUEST_BODY_BYTES: int = 1_048_576  # 1 MB default application-level limit

    # Database parameters
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "queless_user"
    POSTGRES_PASSWORD: str = "queless_password"
    POSTGRES_DB: str = "queless_db"
    DATABASE_URL: str | None = None

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        valid_envs = {"development", "staging", "production"}
        v_clean = v.lower().strip()
        if v_clean not in valid_envs:
            raise ValueError(f"ENVIRONMENT must be one of: {valid_envs}")
        return v_clean

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail closed if production/staging runs with default or weak secret key."""
        insecure_defaults = {
            "dev-insecure-secret-key-replace-in-production-min-32-chars-long!",
            "secret",
            "changeme",
            "password",
        }
        if self.ENVIRONMENT in ("production", "staging"):
            if not self.AUTH_SECRET_KEY or self.AUTH_SECRET_KEY in insecure_defaults:
                raise ValueError(
                    "Production/staging environment requires a non-default, "
                    "secure AUTH_SECRET_KEY. Current value is insecure or default."
                )
            if len(self.AUTH_SECRET_KEY) < 32:
                raise ValueError(
                    f"AUTH_SECRET_KEY must be at least 32 characters in {self.ENVIRONMENT} mode."
                )
            if self.GOOGLE_AUTH_ENABLED:
                invalid_google_ids = {"", "your-google-client-id", "placeholder", "changeme"}
                if not self.GOOGLE_CLIENT_ID or self.GOOGLE_CLIENT_ID.strip() in invalid_google_ids:
                    raise ValueError(
                        f"GOOGLE_CLIENT_ID must be configured in {self.ENVIRONMENT} "
                        "mode when GOOGLE_AUTH_ENABLED is True."
                    )
        return self

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy asynchronous database connection string."""
        if self.DATABASE_URL:
            url = self.DATABASE_URL.strip()
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgresql+psycopg://"):
                return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
            return url
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy synchronous database connection string for migrations."""
        if self.DATABASE_URL:
            url = self.DATABASE_URL.strip()
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            if url.startswith("postgresql+asyncpg://"):
                return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
            elif url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+psycopg://", 1)
            return url
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    """Singleton getter for application settings."""
    return Settings()
