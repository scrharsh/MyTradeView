"""Pytest shared fixtures for unit and integration testing."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.storage.db import create_engine_and_factory, init_db
from src.storage.repository import StateRepository


@pytest_asyncio.fixture
async def test_engine() -> AsyncEngine:
    """Create in-memory SQLite engine for tests."""
    engine, _ = create_engine_and_factory("sqlite+aiosqlite:///:memory:", echo=False)
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine: AsyncEngine) -> AsyncSession:
    """Provide an isolated database session rolled back after test."""
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def state_repo(test_session: AsyncSession) -> StateRepository:
    """Provide a StateRepository bound to test session."""
    return StateRepository(test_session)
