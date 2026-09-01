"""Shared async infrastructure for PostgreSQL collaboration repositories."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from time import time_ns
from typing import LiteralString, TypeAlias, cast
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from nanobot.collaboration.postgres.session import PostgresSession

_Row: TypeAlias = dict[str, object]
PostgresConnection: TypeAlias = AsyncConnection[tuple[object, ...]]


class PostgresRepositoryBase:
    """Lifecycle, RLS transaction, and typed row helpers shared by repository mixins."""

    def __init__(self, session: PostgresSession) -> None:
        self._session = session

    async def initialize(self) -> None:
        """Open the PostgreSQL pool and apply configured migrations."""
        await self._session.initialize()

    async def aclose(self) -> None:
        """Close the PostgreSQL pool."""
        await self._session.aclose()

    @asynccontextmanager
    async def _actor_transaction(self, user_id: str) -> AsyncGenerator[PostgresConnection, None]:
        """Yield an actor-scoped transaction for repository implementations."""
        async with self._session.transaction(user_id) as connection:
            yield connection

    @asynccontextmanager
    async def _identity_transaction(
        self, channel: str, sender_id: str
    ) -> AsyncGenerator[PostgresConnection, None]:
        """Yield an exact identity-bootstrap transaction for repository implementations."""
        async with self._session.identity_transaction(channel, sender_id) as connection:
            yield connection

    @staticmethod
    def _new_id() -> str:
        """Return an unpredictable, storage-compatible identifier."""
        return uuid4().hex

    @staticmethod
    def _now_ms() -> int:
        """Return the current Unix timestamp with millisecond precision."""
        return time_ns() // 1_000_000

    @staticmethod
    async def _fetch_one(
        connection: PostgresConnection,
        query: LiteralString,
        params: Sequence[object] = (),
    ) -> _Row | None:
        """Execute static SQL and return one strictly dictionary-shaped row."""
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            row = await cursor.fetchone()
        return _as_row(row) if row is not None else None

    @staticmethod
    async def _fetch_all(
        connection: PostgresConnection,
        query: LiteralString,
        params: Sequence[object] = (),
    ) -> list[_Row]:
        """Execute static SQL and return strictly dictionary-shaped rows."""
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
        return [_as_row(row) for row in rows]


def _as_row(value: object) -> _Row:
    if not isinstance(value, dict):
        raise TypeError("PostgreSQL row factory did not return a dictionary")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise TypeError("PostgreSQL row factory did not return a string-keyed dictionary")
    return cast(_Row, mapping)
