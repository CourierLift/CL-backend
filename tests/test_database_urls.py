import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

from backend.settings import Settings


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_provider_database_url_selects_installed_driver(scheme):
    suffix = "user:p%40ss%25word@db.example.com/courier_lifts?sslmode=require"
    settings = Settings(CL_DATABASE_URL=f"{scheme}://{suffix}")

    assert settings.CL_DATABASE_URL == f"postgresql+psycopg://{suffix}"
    engine = create_engine(settings.CL_DATABASE_URL)
    assert engine.dialect.driver == "psycopg"
    assert engine.url.password == "p@ss%word"
    assert engine.url.query["sslmode"] == "require"
    engine.dispose()


@pytest.mark.parametrize(
    "url",
    ["sqlite:///:memory:", "postgresql+psycopg://user:pass@db.example.com/app"],
)
def test_explicit_database_driver_is_preserved(url):
    assert Settings(CL_DATABASE_URL=url).CL_DATABASE_URL == url


def test_provider_url_from_environment_is_normalized(monkeypatch):
    monkeypatch.setenv("CL_DATABASE_URL", "postgresql://user:pass@db.example.com/app")
    assert Settings().CL_DATABASE_URL.startswith("postgresql+psycopg://")


def test_alembic_accepts_encoded_provider_credentials_without_connecting():
    environment = {
        **os.environ,
        "CL_APP_ENV": "test",
        "CL_DATABASE_URL": "postgresql://user:p%40ss%25word@db.example.com/app",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE users" in result.stdout
    assert "0004_pricing_snapshot" in result.stdout
