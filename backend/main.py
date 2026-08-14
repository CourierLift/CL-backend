"""Courier Lifts FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401 - registers SQLAlchemy models
from .database import Base, engine
from .orders import router as orders_router
from .routes.auth_routes_jwt import router as auth_router
from .routes.rewards_routes import router as rewards_router
from .settings import settings
from .tracking import router as tracking_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # MVP convenience: create a fresh schema. Existing databases need a real
    # migration before deployment because create_all does not alter tables.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Courier Lifts MVP",
    version="0.1.0",
    lifespan=lifespan,
)

allowed_origins = list(
    dict.fromkeys(
        [settings.CL_FRONTEND_ORIGIN, "http://localhost:5173"]
    )
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "env": settings.CL_APP_ENV}


app.include_router(auth_router)
app.include_router(rewards_router)
app.include_router(orders_router)
app.include_router(tracking_router)
