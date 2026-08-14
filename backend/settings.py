"""Application configuration loaded from CL-prefixed environment variables."""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field


load_dotenv(override=False)


class Settings(BaseModel):
    CL_APP_ENV: str = Field(
        default_factory=lambda: os.getenv("CL_APP_ENV", "development")
    )
    CL_SECRET_KEY: str = Field(
        default_factory=lambda: os.getenv(
            "CL_SECRET_KEY", "local-development-only-change-before-deploy"
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


settings = Settings()
