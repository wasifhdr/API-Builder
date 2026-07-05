import uuid

import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.user import User

# Isolated from the dev DB/Redis so tests never touch real data:
# a separate Postgres database, and Redis logical db index 1 instead of 0.
TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/apibuilder_test"
TEST_REDIS_URL = settings.redis_url.rsplit("/", 1)[0] + "/1"


@pytest_asyncio.fixture
async def engine():
    # Function-scoped (not session-scoped): pytest-asyncio gives each test its
    # own event loop by default, and asyncpg connections can't cross loops.
    # Tables already exist in apibuilder_test after the first run — create_all
    # is a fast no-op on subsequent tests.
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncSession:
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine):
    yield
    # TRUNCATE ... CASCADE (rather than per-table deletes) sidesteps needing a
    # dependency order — bkash_sms_receipts and payment_transactions reference
    # each other, so no linear delete order exists. Base.metadata.tables (not
    # sorted_tables) avoids triggering the same cycle warning while sorting.
    table_names = ", ".join(t.name for t in Base.metadata.tables.values())
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))


@pytest_asyncio.fixture
async def redis():
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def make_user(db: AsyncSession):
    async def _make(email: str | None = None) -> User:
        user = User(
            google_sub=f"sub-{uuid.uuid4()}",
            email=email or f"{uuid.uuid4()}@example.com",
            name="Test User",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    return _make
