# --- backend/main.py --------------------------------------------------------
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .settings import settings
from .database import Base, engine
from . import models  # ensure models are loaded before create_all

app = FastAPI(title="Courier Lifts MVP")

# CORS so the frontend (http://localhost:5173) can call the API [Glossary: CORS]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.CL_FRONTEND_ORIGIN, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create DB tables on boot (dev convenience)
Base.metadata.create_all(bind=engine)

@app.get("/health")
def health():
    return {"ok": True, "env": settings.APP_ENV}

# Routers
from .routes.auth_routes_jwt import router as auth_router
from .routes.rewards_routes import router as rewards_router
from . import orders

# Register JWT auth and rewards routers
app.include_router(auth_router)
app.include_router(rewards_router)
app.include_router(orders.router)
# ---------------------------------------------------------------------------
