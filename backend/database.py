"""Canonical SQLAlchemy engine, session factory, and request dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from .settings import settings


DATABASE_URL = settings.CL_DATABASE_URL
SQLITE_CONNECT_ARGS = (
    {"check_same_thread": False, "timeout": 30}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

ENGINE_OPTIONS = {
    "connect_args": SQLITE_CONNECT_ARGS,
    "pool_pre_ping": True,
}
if DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}:
    ENGINE_OPTIONS["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **ENGINE_OPTIONS)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)
Base = declarative_base()


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
