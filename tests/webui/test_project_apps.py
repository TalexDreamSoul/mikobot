from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agent import plugins as agent_plugins
from nanobot.agent.plugins import AGENT_PLUGIN_SCHEMA, set_agent_plugin_enabled
from nanobot.collaboration import (
    AsyncLocalCollaborationRepository,
    CollaborationStore,
    Project,
    ProjectAppGrant,
)
from nanobot.webui.project_apps import (
    AppCatalogEntry,
    ProjectAppCatalog,
    ProjectAppError,
    reconcile_project_apps,
)


@pytest.fixture(autouse=True)
def _isolate_plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_plugins, "get_config_path", lambda: tmp_path / "config" / "config.json"
    )


def _catalog(
    *entries: AppCatalogEntry,
    skills: tuple[str, ...] = (),
    mcp_servers: tuple[str, ...] = (),
) -> ProjectAppCatalog:
    return ProjectAppCatalog(
        entries, available_skills=skills, available_mcp_servers=mcp_servers
    )


def _app(
    name: str = "studio",
    *,
    revision: str = "rev-1",
    skills: tuple[str, ...] = ("poster",),
    mcp_servers: tuple[str, ...] = ("studio--assets",),
    enabled: bool = True,
) -> AppCatalogEntry:
    return AppCatalogEntry(
        name, name.title(), "An app.", revision, enabled, skills, mcp_servers
    )


def _grant(name: str, revision: str, skills: tuple[str, ...], servers: tuple[str, ...]):
    return ProjectAppGrant(name, revision, skills, servers)


def _required(project: Project | None) -> Project:
    assert project is not None
    return project


def _project(**fields: object) -> Project:
    base: dict[str, object] = {
        "id": "col_project",
        "name": "Release train",
        "workspace_path": "/tmp/project",
        "created_by_user_id": "usr_owner",
        "created_at_ms": 1,
        "updated_at_ms": 1,
    }
    base.update(fields)
    return Project(**base)  # type: ignore[arg-type]


def _install_plugin(workspace: Path, name: str = "studio", *, skill: str = "poster") -> None:
    root = workspace / "plugins" / name
    (root / "skills" / skill).mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({"$schema": AGENT_PLUGIN_SCHEMA, "name": name, "description": "An app."}),
        encoding="utf-8",
    )
    (root / "skills" / skill / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: Poster skill.\n---\n\nBody\n", encoding="utf-8"
    )


def test_join_pins_the_installed_revision_and_grants_its_capabilities() -> None:
    """Approving an app records what it gave the project, not just which app it is."""
    catalog = _catalog(_app(), skills=("poster", "unrelated"), mcp_servers=("studio--assets",))
    project = _project(
        allowed_skills=("unrelated",), allowed_mcp_servers=("studio--assets", "other")
    )

    change = catalog.join(project, "studio")

    assert change.allowed_skills == ("poster", "unrelated")
    assert change.allowed_mcp_servers == ("other", "studio--assets")
    assert change.app_grants == (_grant("studio", "rev-1", ("poster",), ("studio--assets",)),)


def test_join_leaves_an_unrestricted_project_unrestricted() -> None:
    """A project that allows everything keeps allowing everything after joining."""
    catalog = _catalog(_app(), skills=("poster",), mcp_servers=("studio--assets",))

    change = catalog.join(_project(allowed_skills=None, allowed_mcp_servers=None), "studio")

    assert change.allowed_skills is None
    assert change.allowed_mcp_servers is None
    assert [grant.name for grant in change.app_grants] == ["studio"]


def test_join_refuses_a_disabled_app_or_a_stale_revision() -> None:
    """An approval always names the revision the host is actually running."""
    disabled = _catalog(_app(enabled=False), skills=("poster",))
    with pytest.raises(ProjectAppError, match="not enabled"):
        disabled.join(_project(), "studio")

    catalog = _catalog(_app(), skills=("poster",))
    with pytest.raises(ProjectAppError, match="changed since it was reviewed"):
        catalog.join(_project(), "studio", revision="rev-0")
    with pytest.raises(ProjectAppError, match="app not found"):
        catalog.join(_project(), "missing")


def test_leave_takes_back_exactly_what_the_app_granted() -> None:
    """Leaving removes the recorded capabilities and keeps the rest."""
    catalog = _catalog(_app(), skills=("poster", "unrelated"), mcp_servers=("studio--assets",))
    project = _project(
        allowed_skills=("poster", "unrelated"),
        allowed_mcp_servers=("studio--assets",),
        app_grants=(_grant("studio", "rev-1", ("poster",), ("studio--assets",)),),
    )

    change = catalog.leave(project, "studio")

    assert change.allowed_skills == ("unrelated",)
    assert change.allowed_mcp_servers == ()
    assert change.app_grants == ()


def test_a_replaced_app_loses_its_capabilities_until_it_is_approved_again() -> None:
    """Drift revokes; it never upgrades a project onto code nobody reviewed."""
    catalog = _catalog(_app(revision="rev-2"), skills=("poster",), mcp_servers=("studio--assets",))
    project = _project(
        allowed_skills=("poster", "unrelated"),
        allowed_mcp_servers=("studio--assets",),
        app_grants=(_grant("studio", "rev-1", ("poster",), ("studio--assets",)),),
    )

    assert catalog.revoked_allowlists(project) == (("unrelated",), ())

    approved = catalog.join(project, "studio")
    assert approved.allowed_skills == ("poster", "unrelated")
    assert approved.app_grants == (_grant("studio", "rev-2", ("poster",), ("studio--assets",)),)


def test_revoking_from_an_unrestricted_project_materializes_the_restriction() -> None:
    """``None`` cannot subtract, so the first revocation writes the inventory minus the app."""
    catalog = _catalog(
        _app(revision="rev-2"),
        skills=("poster", "unrelated"),
        mcp_servers=("studio--assets", "other"),
    )
    project = _project(
        allowed_skills=None,
        allowed_mcp_servers=None,
        app_grants=(_grant("studio", "rev-1", ("poster",), ("studio--assets",)),),
    )

    assert catalog.revoked_allowlists(project) == (("unrelated",), ("other",))


def test_a_removed_app_still_loses_its_recorded_capabilities() -> None:
    """The grant remembers what it gave, so an uninstalled app can still be taken back."""
    catalog = _catalog(skills=("poster",), mcp_servers=("studio--assets",))
    project = _project(
        allowed_skills=("poster",),
        allowed_mcp_servers=("studio--assets",),
        app_grants=(_grant("studio", "rev-1", ("poster",), ("studio--assets",)),),
    )

    assert catalog.revoked_allowlists(project) == ((), ())


def test_nothing_drifts_for_a_project_that_keeps_its_revision() -> None:
    catalog = _catalog(_app(), skills=("poster",), mcp_servers=("studio--assets",))
    project = _project(
        allowed_skills=("poster",),
        allowed_mcp_servers=("studio--assets",),
        app_grants=(_grant("studio", "rev-1", ("poster",), ("studio--assets",)),),
    )

    assert catalog.revoked_allowlists(project) is None
    assert catalog.revoked_allowlists(_project()) is None


def test_catalog_loads_only_apps_that_contribute_something(tmp_path: Path) -> None:
    """Discovery reports what the host really offers, and excludes the rest."""
    workspace = tmp_path / "host"
    _install_plugin(workspace)
    set_agent_plugin_enabled(workspace, "studio", True)

    catalog = ProjectAppCatalog.load(
        workspace, available_skills=["poster", "unrelated"], available_mcp_servers=[]
    )

    assert [entry.name for entry in catalog.entries] == ["studio"]
    entry = catalog.entries[0]
    assert entry.enabled is True
    assert entry.skills == ("poster",)
    assert entry.mcp_servers == ()
    assert entry.revision

    set_agent_plugin_enabled(workspace, "studio", False)
    assert ProjectAppCatalog.load(
        workspace, available_skills=["poster"], available_mcp_servers=[]
    ).entries[0].enabled is False


@pytest.mark.asyncio
async def test_reconcile_revokes_only_the_projects_that_drifted(tmp_path: Path) -> None:
    """The sweep takes back what changed, and leaves every other project alone."""
    store = CollaborationStore(tmp_path / "collaboration")
    repository = AsyncLocalCollaborationRepository(store)
    owner, project = store.ensure_local_owner(tmp_path / "agent")
    store.update_user_admin(owner.id, True)
    peer = store.create_user("peer")
    other = store.create_project(peer.id, "Peer project", tmp_path / "peer")
    store.update_project(
        project.id,
        owner.id,
        allowed_skills=["poster", "unrelated"],
        app_grants=[_grant("studio", "rev-1", ("poster",), ())],
    )
    store.update_project(
        other.id,
        peer.id,
        allowed_skills=["poster"],
        app_grants=[_grant("studio", "rev-2", ("poster",), ())],
    )
    catalog = _catalog(
        _app(revision="rev-2"), skills=("poster", "unrelated"), mcp_servers=()
    )

    changed = await reconcile_project_apps(repository, tmp_path / "agent", catalog)

    assert changed == 1
    revoked = _required(store.get_project(owner.id, project.id))
    assert revoked.allowed_skills == ("unrelated",)
    assert revoked.app_grant("studio") is not None
    kept = _required(store.get_project(owner.id, other.id))
    assert kept.allowed_skills == ("poster",)
