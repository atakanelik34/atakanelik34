"""Typed application settings, loaded from environment variables.

All configuration enters the process here. Secrets are `SecretStr` so they never
appear in reprs or logs. Production refuses to start with unsafe values.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    S3 = "s3"
    LOCAL = "local"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "idp"
    pipeline_version: str = "0.1.0"

    # --- HTTP ---------------------------------------------------------------
    api_prefix: str = "/api/v1"
    api_docs_enabled: bool = True
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON

    # --- PostgreSQL ---------------------------------------------------------
    database_url: SecretStr = SecretStr("postgresql+asyncpg://idp:idp@localhost:5432/idp")
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_echo: bool = False

    # --- Redis / queue ------------------------------------------------------
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    worker_max_jobs: int = Field(default=4, ge=1)
    worker_heartbeat_seconds: int = Field(default=15, ge=1)

    # --- Object storage -----------------------------------------------------
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_bucket: str = "idp-documents"
    storage_local_root: str = "./var/storage"
    storage_auto_create_bucket: bool = False
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_sse: str | None = None  # e.g. "AES256" or "aws:kms"
    signed_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # --- Ingestion ------------------------------------------------------------
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)

    # --- Job execution --------------------------------------------------------
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_seconds: float = Field(default=10.0, gt=0)
    job_retry_max_seconds: float = Field(default=600.0, gt=0)
    # A RUNNING job whose lease expired is presumed dead and is reclaimed.
    job_lease_seconds: int = Field(default=900, ge=30)
    sweeper_queued_grace_seconds: int = Field(default=60, ge=5)
    probe_timeout_seconds: float = Field(default=120.0, gt=0)
    probe_max_pages: int = Field(default=2000, ge=1)
    probe_memory_limit_mb: int = Field(default=2048, ge=256)
    worker_tmp_dir: str | None = None

    # --- Digitization / OCR (phase 3) -------------------------------------------
    # "none" is honest: image-only pages are marked `ocr: not_configured`.
    ocr_engine: str = Field(default="none", pattern="^(none|tesseract|mock)$")
    ocr_languages: str = "eng+deu"
    tesseract_cmd: str = "tesseract"
    ocr_timeout_seconds: float = Field(default=120.0, gt=0)
    render_dpi: int = Field(default=150, ge=72, le=300)
    ocr_dpi: int = Field(default=300, ge=150, le=600)
    # Native text below this quality is treated as unreadable and OCR'd instead.
    native_text_min_quality: float = Field(default=0.5, ge=0, le=1)

    # --- Auth ---------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "idp"
    access_token_ttl_minutes: int = Field(default=30, ge=1, le=24 * 60)
    login_rate_limit_attempts: int = Field(default=10, ge=1)
    login_rate_limit_window_seconds: int = Field(default=300, ge=10)

    @model_validator(mode="before")
    @classmethod
    def _split_cors(cls, data: object) -> object:
        if isinstance(data, dict):
            raw = data.get("cors_origins") or data.get("CORS_ORIGINS")
            if isinstance(raw, str):
                origins = [o.strip() for o in raw.split(",") if o.strip()]
                data = {**data, "cors_origins": origins}
                data.pop("CORS_ORIGINS", None)
        return data

    @model_validator(mode="after")
    def _enforce_safe_configuration(self) -> Self:
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be set and at least {MIN_JWT_SECRET_LENGTH} characters long"
            )
        if self.environment is Environment.PRODUCTION:
            if self.storage_backend is StorageBackend.LOCAL:
                raise ValueError("STORAGE_BACKEND=local is not allowed in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS='*' is not allowed in production")
        if self.storage_backend is StorageBackend.S3 and (
            self.s3_access_key_id is None or self.s3_secret_access_key is None
        ):
            raise ValueError("S3 storage requires S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class DatabaseSettings(BaseSettings):
    """The subset needed by migrations and operational CLI commands.

    Kept separate so running `alembic upgrade` or `idp bootstrap` does not
    require (and therefore does not receive) the JWT secret or storage keys.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: SecretStr
    database_echo: bool = False


def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()
