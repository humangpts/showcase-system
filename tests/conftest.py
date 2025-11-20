import pytest
import os
import asyncio
from typing import AsyncGenerator
from uuid import uuid4
from datetime import datetime

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv

from app.core.database import Base, init_models
from app.users.models import User
from app.projects.models import Project

# Загрузка настроек
load_dotenv(".env")
load_dotenv(".env.test", override=True)

DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres")
DB_HOST = os.getenv("POSTGRES_SERVER", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_TEST_DB")

TEST_DATABASE_URL = (
    f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# --- БАЗОВЫЕ ФИКСТУРЫ (Scope = Function) ---


@pytest.fixture(scope="function")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Создает движок. Scope=Function гарантирует изоляцию."""
    if "prod" in DB_NAME.lower() and "test" not in DB_NAME.lower():  # type: ignore
        raise RuntimeError(f"CRITICAL: PROD DB DETECTED: {DB_NAME}")

    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,  # Отключаем пулинг, чтобы не держать соединения
    )

    init_models()

    # Пересоздаем таблицы перед КАЖДЫМ тестом
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Удаляем после теста
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """
    Простая сессия без вложенных транзакций.
    """
    session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    async with session_maker() as session:
        yield session
        # В конце теста просто закрываем, engine fixture удалит таблицы
        await session.close()


# --- ДАННЫЕ ---


@pytest.fixture(scope="function")
async def user(db_session: AsyncSession):
    user_id = uuid4()
    new_user = User(
        id=user_id,  # type: ignore
        email=f"test_{user_id}@example.com",  # type: ignore
        hashed_password="hashed",  # type: ignore
        is_active=True,  # type: ignore
        is_superuser=False,  # type: ignore
        is_verified=True,  # type: ignore
        created_at=datetime.now(),  # type: ignore
    )
    db_session.add(new_user)
    await db_session.commit()
    return new_user


@pytest.fixture(scope="function")
async def project(db_session: AsyncSession, user: User):
    project_id = uuid4()
    new_project = Project(
        id=project_id,
        name="Test Project",
        description="Desc",
        icon="building",
        visibility="public",
        created_by=user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(new_project)
    await db_session.commit()
    return new_project
