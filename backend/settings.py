"""Application configuration loaded from CL-prefixed environment variables."""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


load_dotenv(override=False)


DEVELOPMENT_SECRET_KEY = "local-development-only-change-before-deploy"
INSECURE_SECRET_KEYS = {
    DEVELOPMENT_SECRET_KEY,
    "replace-with-a-long-random-secret",
}


class Settings(BaseModel):
    CL_APP_ENV: str = Field(
        default_factory=lambda: os.getenv("CL_APP_ENV", "development")
    )
    CL_SECRET_KEY: str = Field(
        default_factory=lambda: os.getenv(
            "CL_SECRET_KEY", DEVELOPMENT_SECRET_KEY
        )
    )
    CL_JWT_ALGORITHM: str = Field(
        default_factory=lambda: os.getenv("CL_JWT_ALGORITHM", "HS256")
    )
    CL_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default_factory=lambda: int(
            os.getenv("CL_ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
        )
    )
    CL_DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "CL_DATABASE_URL", "sqlite:///./courier_lifts.db"
        )
    )
    CL_FRONTEND_ORIGIN: str = Field(
        default_factory=lambda: os.getenv(
            "CL_FRONTEND_ORIGIN", "http://localhost:5173"
        )
    )
    CL_DEVELOPMENT_FALLBACK_MILES: float = Field(
        default_factory=lambda: float(
            os.getenv("CL_DEVELOPMENT_FALLBACK_MILES", "8.0")
        ),
        gt=0,
    )
    CL_GOOGLE_MAPS_API_KEY: str | None = Field(
        default_factory=lambda: os.getenv("CL_GOOGLE_MAPS_API_KEY")
    )
    CL_AUTH_REGISTER_RATE_LIMIT: int = Field(
        default_factory=lambda: int(
            os.getenv("CL_AUTH_REGISTER_RATE_LIMIT", "10")
        ),
        gt=0,
    )
    CL_AUTH_LOGIN_RATE_LIMIT: int = Field(
        default_factory=lambda: int(
            os.getenv("CL_AUTH_LOGIN_RATE_LIMIT", "20")
        ),
        gt=0,
    )
    CL_AUTH_RATE_WINDOW_SECONDS: int = Field(
        default_factory=lambda: int(
            os.getenv("CL_AUTH_RATE_WINDOW_SECONDS", "60")
        ),
        gt=0,
    )
    CL_OBJECT_STORAGE_BACKEND: str = Field(
        default_factory=lambda: os.getenv("CL_OBJECT_STORAGE_BACKEND", "local")
    )
    CL_OBJECT_STORAGE_PATH: str = Field(
        default_factory=lambda: os.getenv(
            "CL_OBJECT_STORAGE_PATH", str(Path("./object_storage"))
        )
    )
    CL_S3_BUCKET: str | None = Field(
        default_factory=lambda: os.getenv("CL_S3_BUCKET")
    )
    CL_S3_REGION: str = Field(
        default_factory=lambda: os.getenv("CL_S3_REGION", "us-east-1")
    )
    CL_S3_ENDPOINT_URL: str | None = Field(
        default_factory=lambda: os.getenv("CL_S3_ENDPOINT_URL")
    )
    CL_PROOF_MAX_BYTES: int = Field(
        default_factory=lambda: int(os.getenv("CL_PROOF_MAX_BYTES", str(10 * 1024 * 1024))),
        gt=0,
    )

    @model_validator(mode="after")
    def validate_runtime_posture(self) -> "Settings":
        backend = self.CL_OBJECT_STORAGE_BACKEND.strip().lower()
        if backend not in {"local", "s3"}:
            raise ValueError("CL_OBJECT_STORAGE_BACKEND must be 'local' or 's3'")

        if self.CL_APP_ENV.strip().lower() not in {"prod", "production"}:
            return self

        secret = self.CL_SECRET_KEY.strip()
        if secret in INSECURE_SECRET_KEYS or len(secret) < 32:
            raise ValueError(
                "CL_SECRET_KEY must be a new production secret of at least 32 characters"
            )
        if not self.CL_DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("Production CL_DATABASE_URL must use PostgreSQL")
        if backend != "s3" or not (self.CL_S3_BUCKET or "").strip():
            raise ValueError("Production proof storage requires S3-compatible object storage")
        if not (self.CL_GOOGLE_MAPS_API_KEY or "").strip():
            raise ValueError("Production address pricing requires CL_GOOGLE_MAPS_API_KEY")

        origin = self.CL_FRONTEND_ORIGIN.strip().rstrip("/")
        parsed = urlparse(origin)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Production CL_FRONTEND_ORIGIN must be an HTTPS origin")
        if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
            raise ValueError("Production CL_FRONTEND_ORIGIN cannot use localhost")
        if parsed.path or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("CL_FRONTEND_ORIGIN must contain only scheme, host, and optional port")
        self.CL_FRONTEND_ORIGIN = origin
        return self


settings = Settings()
