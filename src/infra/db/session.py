"""Асинхронное подключение к БД (PostgreSQL asyncpg и SQLite aiosqlite)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        # Убеждаемся, что директория для sqlite-файла существует
        if ":///" in database_url and ":memory:" not in database_url:
            db_path = Path(database_url.split(":///", 1)[1])
            db_path.parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30.0
        engine = create_async_engine(database_url, echo=echo, connect_args=connect_args)

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine

    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
