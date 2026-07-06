from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import auth, billing, me, recordings, workflows, ws
from app.config import settings
from app.db import engine
from app.redis import redis_client

api_router = APIRouter(prefix="/api")


@api_router.get("/health")
async def health() -> dict:
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    redis_ok = True
    try:
        await redis_client.ping()
    except Exception:
        redis_ok = False

    return {"status": "ok" if db_ok and redis_ok else "degraded", "db": db_ok, "redis": redis_ok}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await redis_client.aclose()
    await engine.dispose()


app = FastAPI(title="API Builder", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(auth.router, prefix="/api")
app.include_router(me.router, prefix="/api")
app.include_router(billing.router, prefix="/api")
app.include_router(recordings.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(ws.router, prefix="/api")
