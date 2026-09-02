"""Concrete normalized PostgreSQL collaboration repository."""

from __future__ import annotations

from nanobot.collaboration.postgres.base import PostgresRepositoryBase
from nanobot.collaboration.postgres.bots import PostgresBotsMixin
from nanobot.collaboration.postgres.context import PostgresContextMixin
from nanobot.collaboration.postgres.private import PostgresPrivateMixin
from nanobot.collaboration.postgres.projects import PostgresProjectsMixin
from nanobot.collaboration.repository import CollaborationRepository


class PostgresCollaborationRepository(
    PostgresBotsMixin,
    PostgresPrivateMixin,
    PostgresProjectsMixin,
    PostgresContextMixin,
    PostgresRepositoryBase,
):
    """Normalized asynchronous PostgreSQL collaboration persistence."""


def _assert_collaboration_repository_implementation(
    implementation: type[CollaborationRepository],
) -> None:
    """Make protocol conformance a static type-checking requirement."""


_assert_collaboration_repository_implementation(PostgresCollaborationRepository)
