"""Courier Lifts FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import models  # noqa: F401 - registers SQLAlchemy models
from .database import Base, engine
from .orders import router as orders_router
from .routes.auth_routes_jwt import router as auth_router
from .routes.courier_orders import router as courier_orders_router
from .routes.order_detail import router as order_detail_router
from .routes.proofs import router as proofs_router
from .routes.rewards_routes import router as rewards_router
from .settings import settings
from .tracking import router as tracking_router


PRODUCTION_ENVS = {"prod", "production"}


def cors_origins(app_env: str, frontend_origin: str) -> list[str]:
    origins = [frontend_origin]
    if app_env.strip().lower() not in PRODUCTION_ENVS:
        origins.append("http://localhost:5173")
    return list(dict.fromkeys(origins))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Development/test convenience only. Production schema changes are managed
    # explicitly with Alembic so application startup never mutates production DBs.
    if settings.CL_APP_ENV.strip().lower() not in PRODUCTION_ENVS:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Courier Lifts MVP",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(settings.CL_APP_ENV, settings.CL_FRONTEND_ORIGIN),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "env": settings.CL_APP_ENV}


@app.get("/ready")
def ready() -> dict[str, object]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"ok": True, "database": "reachable"}


app.include_router(auth_router)
app.include_router(rewards_router)
app.include_router(orders_router)
app.include_router(courier_orders_router)
app.include_router(order_detail_router)
app.include_router(proofs_router)
app.include_router(tracking_router)
