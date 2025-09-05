from pydantic import BaseModel
import os

class Settings(BaseModel):
    APP_ENV: str = os.getenv("APP_ENV", "dev")
    CL_SECRET_KEY: str = os.getenv("CL_SECRET_KEY", "change-me")
    CL_ACCESS_MIN: int = int(os.getenv("CL_ACCESS_MIN", "120"))
    CL_DATABASE_URL: str = os.getenv("CL_DATABASE_URL", "sqlite:///./db.sqlite3")
    CL_FRONTEND_ORIGIN: str = os.getenv("CL_FRONTEND_ORIGIN", "http://localhost:5173")

settings = Settings()
