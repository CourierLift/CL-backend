"""Application configuration loaded from CL-prefixed environment variables."""

import os

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

    @model_validator(mode="after")
    def validate_production_secret(self) -> "Settings":
        if self.CL_APP_ENV.strip().lower() not in {"prod", "production"}:
            return self

        secret = self.CL_SECRET_KEY.strip()
        if secret in INSECURE_SECRET_KEYS or len(secret) < 32:
            raise ValueError(
                "CL_SECRET_KEY must be a new production secret of at least 32 characters"
            )
        return self


settings = Settings()
