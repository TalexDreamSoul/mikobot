"""Async PostgreSQL pool and transaction-scoped RLS context helpers."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TypeAlias, cast

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from nanobot.collaboration.postgres.migrations import migrate
from nanobot.config.schema import CollaborationConfig

_Row: TypeAlias = tuple[object, ...]
_Connection: TypeAlias = AsyncConnection[_Row]
_Pool: TypeAlias = AsyncConnectionPool[_Connection]


class PostgresSession:
    """Lazily own a collaboration pool and bind RLS settings per transaction."""

    def __init__(self, config: CollaborationConfig) -> None:
        if config.backend != "postgres":
            raise ValueError("PostgresSession requires the postgres collaboration backend")
        if config.postgres_dsn is None:
            raise ValueError("postgres collaboration backend requires a DSN")
        self._dsn = config.postgres_dsn
        self._migration_dsn = config.postgres_migration_dsn
        self._min_pool_size = config.postgres_min_pool_size
        self._max_pool_size = config.postgres_max_pool_size
        self._command_timeout_ms = config.command_timeout_ms
        self._auto_migrate = config.auto_migrate
        self._pool: _Pool | None = None
        self._pool_lock = asyncio.Lock()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Open the runtime pool and migrate through the separate privileged DSN once."""
        async with self._initialize_lock:
            if self._initialized:
                return
            pool = await self._open_pool()
            try:
                if self._auto_migrate:
                    assert self._migration_dsn is not None
                    async with pool.connection() as runtime_connection:
                        cursor = await runtime_connection.execute("SELECT current_user")
                        row = await cursor.fetchone()
                        assert row is not None
                        runtime_role = cast(str, row[0])
                    async with await AsyncConnection.connect(
                        self._migration_dsn
                    ) as migration_connection:
                        await migrate(migration_connection, runtime_role)
            except BaseException:
                self._initialized = False
                async with self._pool_lock:
                    if self._pool is pool:
                        self._pool = None
                        try:
                            await pool.close()
                        except BaseException:
                            pass
                raise
            self._initialized = True

    async def aclose(self) -> None:
        """Idempotently close the pool and reset initialization state."""
        async with self._initialize_lock:
            self._initialized = False
            async with self._pool_lock:
                pool = self._pool
                self._pool = None
            if pool is not None:
                await pool.close()

    @asynccontextmanager
    async def transaction(
        self,
        user_id: str,
        *,
        identity_channel: str | None = None,
        identity_sender_id: str | None = None,
    ) -> AsyncGenerator[_Connection, None]:
        """Yield a connection whose tenant identity is local to this transaction."""
        if (identity_channel is None) != (identity_sender_id is None):
            raise ValueError("identity channel and sender ID must be provided together")
        pool = await self._open_pool()
        async with pool.connection() as connection:
            async with connection.transaction():
                await self._set_local(connection, "search_path", "pg_catalog")
                await self._set_local(connection, "nanobot.user_id", user_id)
                await self._set_local(
                    connection, "statement_timeout", f"{self._command_timeout_ms}ms"
                )
                if identity_channel is not None and identity_sender_id is not None:
                    await self._set_local(
                        connection, "nanobot.identity_channel", identity_channel
                    )
                    await self._set_local(
                        connection, "nanobot.identity_sender_id", identity_sender_id
                    )
                yield connection

    @asynccontextmanager
    async def identity_transaction(
        self, channel: str, sender_id: str
    ) -> AsyncGenerator[_Connection, None]:
        """Yield an identity-lookup transaction with only exact bootstrap settings."""
        pool = await self._open_pool()
        async with pool.connection() as connection:
            async with connection.transaction():
                await self._set_local(connection, "search_path", "pg_catalog")
                await self._set_local(connection, "nanobot.user_id", "")
                await self._set_local(
                    connection, "statement_timeout", f"{self._command_timeout_ms}ms"
                )
                await self._set_local(connection, "nanobot.identity_channel", channel)
                await self._set_local(connection, "nanobot.identity_sender_id", sender_id)
                yield connection

    async def _open_pool(self) -> _Pool:
        pool = self._pool
        if pool is not None:
            return pool
        async with self._pool_lock:
            pool = self._pool
            if pool is None:
                pool = cast(
                    _Pool,
                    AsyncConnectionPool(
                        conninfo=self._dsn,
                        min_size=self._min_pool_size,
                        max_size=self._max_pool_size,
                        open=False,
                    ),
                )
                try:
                    await pool.open()
                except BaseException:
                    try:
                        await pool.close()
                    except BaseException:
                        pass
                    raise
                self._pool = pool
            return pool

    @staticmethod
    async def _set_local(connection: _Connection, key: str, value: str) -> None:
        await connection.execute("SELECT pg_catalog.set_config(%s, %s, true)", (key, value))
