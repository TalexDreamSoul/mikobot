"""Project app grants: joining a host app to a project at one immutable revision.

An app is an Agent Plugin installed on the host. Its capabilities are the skills
and MCP servers it contributes, and its ``revision`` is the content fingerprint
the host verified before enabling it.

A project approves one app per grant: the revision it approved and the
capabilities it was given. When the installed revision changes — or the app is
disabled or removed — the host takes those capabilities back, so an app update
can never silently change what a project runs. Approving the app again re-pins
the revision and restores its current capabilities.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from nanobot.agent.plugins import discover_agent_plugins
from nanobot.collaboration import Project, ProjectAppGrant
from nanobot.collaboration.repository import CollaborationRepository


@dataclass(frozen=True, slots=True)
class AppCatalogEntry:
    """One host app a project can join, and what it would contribute."""

    name: str
    display_name: str
    description: str
    revision: str
    enabled: bool
    skills: tuple[str, ...]
    mcp_servers: tuple[str, ...]

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (*self.skills, *self.mcp_servers)


@dataclass(frozen=True, slots=True)
class ProjectAppChange:
    """The project fields one app join or leave produces."""

    allowed_skills: tuple[str, ...] | None
    allowed_mcp_servers: tuple[str, ...] | None
    app_grants: tuple[ProjectAppGrant, ...]


@dataclass(frozen=True, slots=True)
class ProjectAppError(Exception):
    """A join or leave the host must refuse, with the status the API should use."""

    status: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def _granted(
    current: Sequence[str] | None, added: Sequence[str]
) -> tuple[str, ...] | None:
    """Add an app's capability names to a project allowlist.

    ``None`` means the project already allows everything, so joining an app
    changes nothing about what it may use; only the approval is recorded.
    """
    if current is None:
        return None
    return tuple(sorted(set(current) | set(added)))


def _revoked(
    current: Sequence[str] | None, removed: Sequence[str], available: Sequence[str]
) -> tuple[str, ...] | None:
    """Take an app's capability names back out of a project allowlist.

    An unrestricted project cannot subtract from ``None``, so the first
    revocation materializes the inventory minus the revoked names: the project
    stays fail-closed until the app is approved again.
    """
    if not removed:
        return tuple(current) if current is not None else None
    if current is None:
        return tuple(sorted(set(available) - set(removed)))
    return tuple(sorted(set(current) - set(removed)))


class ProjectAppCatalog:
    """The host's joinable apps, resolved against what the host now runs."""

    def __init__(
        self,
        entries: Sequence[AppCatalogEntry],
        *,
        available_skills: Sequence[str],
        available_mcp_servers: Sequence[str],
    ) -> None:
        self._entries = {entry.name: entry for entry in entries}
        self.entries = tuple(entries)
        self.available_skills = tuple(available_skills)
        self.available_mcp_servers = tuple(available_mcp_servers)

    @classmethod
    def load(
        cls,
        workspace: Path,
        *,
        available_skills: Sequence[str],
        available_mcp_servers: Sequence[str],
    ) -> ProjectAppCatalog:
        """Discover the installed apps that contribute something to a project.

        Capabilities the host does not actually offer are left out, so a grant
        can only ever name capabilities the project could be allowed to use.
        """
        skills = set(available_skills)
        servers = set(available_mcp_servers)
        entries: list[AppCatalogEntry] = []
        for plugin in discover_agent_plugins(workspace):
            if plugin.revision is None:
                continue
            contributed_skills = tuple(sorted(set(plugin.skills) & skills))
            contributed_servers = tuple(sorted(set(plugin.mcp_servers) & servers))
            if not contributed_skills and not contributed_servers:
                continue
            entries.append(
                AppCatalogEntry(
                    plugin.name,
                    plugin.display_name or plugin.name,
                    plugin.description,
                    plugin.revision,
                    plugin.enabled,
                    contributed_skills,
                    contributed_servers,
                )
            )
        entries.sort(key=lambda entry: (entry.display_name.casefold(), entry.name))
        return cls(
            entries,
            available_skills=tuple(sorted(skills)),
            available_mcp_servers=tuple(sorted(servers)),
        )

    def entry(self, name: str) -> AppCatalogEntry | None:
        return self._entries.get(name)

    def payloads(self, project: Project) -> list[dict[str, object]]:
        """Return every joinable app with this project's approval state."""
        payloads: list[dict[str, object]] = []
        for entry in self.entries:
            grant = project.app_grant(entry.name)
            payloads.append(
                {
                    "name": entry.name,
                    "display_name": entry.display_name,
                    "description": entry.description,
                    "revision": entry.revision,
                    "enabled": entry.enabled,
                    "skills": list(entry.skills),
                    "mcp_servers": list(entry.mcp_servers),
                    "approved_revision": grant.revision if grant is not None else None,
                    "approved": grant is not None,
                    "drifted": grant is not None and self._drifted(entry, grant),
                }
            )
        for grant in project.app_grants:
            if self.entry(grant.name) is not None:
                continue
            # The app is gone: the approval remains so it can be shown and taken back.
            payloads.append(
                {
                    "name": grant.name,
                    "display_name": grant.name,
                    "description": "",
                    "revision": None,
                    "enabled": False,
                    "skills": list(grant.skills),
                    "mcp_servers": list(grant.mcp_servers),
                    "approved_revision": grant.revision,
                    "approved": True,
                    "drifted": True,
                }
            )
        return payloads

    def join(
        self, project: Project, name: str, *, revision: str | None = None
    ) -> ProjectAppChange:
        """Approve one app for a project at the revision the host now runs."""
        entry = self.entry(name)
        if entry is None:
            raise ProjectAppError(404, "app not found")
        if not entry.enabled:
            raise ProjectAppError(409, "the app is not enabled on this host")
        if revision is not None and revision != entry.revision:
            raise ProjectAppError(409, "the app changed since it was reviewed")
        grant = ProjectAppGrant(
            entry.name, entry.revision, entry.skills, entry.mcp_servers
        )
        grants = {existing.name: existing for existing in project.app_grants}
        grants[grant.name] = grant
        return ProjectAppChange(
            _granted(project.allowed_skills, grant.skills),
            _granted(project.allowed_mcp_servers, grant.mcp_servers),
            tuple(sorted(grants.values(), key=lambda item: item.name)),
        )

    def leave(self, project: Project, name: str) -> ProjectAppChange:
        """Take an app's capabilities back, keeping whatever else was approved."""
        grant = project.app_grant(name)
        if grant is None:
            return ProjectAppChange(
                project.allowed_skills, project.allowed_mcp_servers, project.app_grants
            )
        return ProjectAppChange(
            _revoked(project.allowed_skills, grant.skills, self.available_skills),
            _revoked(project.allowed_mcp_servers, grant.mcp_servers, self.available_mcp_servers),
            tuple(existing for existing in project.app_grants if existing.name != name),
        )

    def revoked_allowlists(
        self, project: Project
    ) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None] | None:
        """Return the allowlists that drop every app whose revision no longer matches.

        ``None`` means the project is already correct: nothing drifted, or it has
        no approvals at all.
        """
        drifted = [
            grant
            for grant in project.app_grants
            if (entry := self.entry(grant.name)) is None or self._drifted(entry, grant)
        ]
        if not drifted:
            return None
        return (
            _revoked(
                project.allowed_skills,
                [name for grant in drifted for name in grant.skills],
                self.available_skills,
            ),
            _revoked(
                project.allowed_mcp_servers,
                [name for grant in drifted for name in grant.mcp_servers],
                self.available_mcp_servers,
            ),
        )

    @staticmethod
    def _drifted(entry: AppCatalogEntry, grant: ProjectAppGrant) -> bool:
        """Return whether the host no longer runs the revision that was approved."""
        return not entry.enabled or entry.revision != grant.revision


async def reconcile_project_apps(
    repository: CollaborationRepository,
    workspace: str | Path,
    catalog: ProjectAppCatalog,
) -> int:
    """Revoke the approvals whose apps no longer match, and report how many changed.

    Runs where the host already owns the app inventory: when an app changes and
    when the gateway starts. A grant is never silently rewritten — only its
    capabilities are taken back, so the project page can show what to approve
    again. Like the built-in automations, this covers every project, so it asks
    for the local owner the same way.
    """
    ensure_owner = getattr(repository, "ensure_local_owner", None)
    list_projects = getattr(repository, "list_all_projects", None)
    if ensure_owner is None or list_projects is None:
        return 0
    try:
        owner, _default_project = await ensure_owner(workspace)
        projects = list(await list_projects(owner.id))
    except Exception:  # noqa: BLE001 - the caller logs and keeps serving
        logger.warning("could not list projects to reconcile their apps")
        return 0
    owner_user_id = owner.id
    changed = 0
    for project in projects:
        if not project.app_grants:
            continue
        revoked = catalog.revoked_allowlists(project)
        if revoked is None:
            continue
        allowed_skills, allowed_mcp_servers = revoked
        if (
            allowed_skills == project.allowed_skills
            and allowed_mcp_servers == project.allowed_mcp_servers
        ):
            continue
        await repository.update_project(
            project.id,
            owner_user_id,
            allowed_skills=allowed_skills,
            allowed_mcp_servers=allowed_mcp_servers,
        )
        changed += 1
    return changed


__all__ = [
    "AppCatalogEntry",
    "ProjectAppCatalog",
    "ProjectAppChange",
    "ProjectAppError",
    "reconcile_project_apps",
]
