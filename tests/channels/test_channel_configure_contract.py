"""Observable contract tests for dependency-free channel configuration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from nanobot.channels.contracts import (
    CHANNEL_INSTANCE_REVISION_FIELD,
    ChannelFieldSpec,
    ChannelInstanceSpec,
    ChannelManagementSpec,
    ChannelSetupSpec,
    channel_configure_instance,
    channel_instance_revision,
    channel_set_config_enabled,
)
from nanobot.channels.plugin import ChannelPlugin


@dataclass
class _RecordingUpdater:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self,
        section: Any,
        values: dict[str, Any],
        *,
        instance_id: str = "default",
    ) -> dict[str, Any]:
        self.calls.append((instance_id, deepcopy(values)))
        updated = deepcopy(section)
        for instance in updated["instances"]:
            if instance["id"] == instance_id:
                instance.update(deepcopy(values))
                return updated
        raise AssertionError(f"unexpected instance {instance_id!r}")


def _instance_specs(section: Any, *, enabled_only: bool = True) -> list[ChannelInstanceSpec]:
    if not isinstance(section, dict):
        return []
    return [
        ChannelInstanceSpec(instance_id=instance["id"], config=instance)
        for instance in section.get("instances", [])
        if not enabled_only or instance.get("enabled", False)
    ]


def _plugin(
    updater: _RecordingUpdater,
    setup: ChannelSetupSpec | None,
) -> ChannelPlugin:
    return ChannelPlugin(
        name="configure_contract",
        display_name="Configure Contract",
        runtime=f"{__name__}:NoRuntime",
        setup=setup,
        management=ChannelManagementSpec(
            multi_instance=True,
            instance_specs=_instance_specs,
            update_instance_config=updater,
            runtime_name=lambda name, instance_id: (
                name if instance_id == "default" else f"{name}.{instance_id}"
            ),
        ),
    )


def _section() -> dict[str, Any]:
    return {
        "preserved_channel_setting": "keep",
        "instances": [
            {
                "id": "default",
                "title": "default title",
                "token": "default-token",
                "members": ["default-member"],
                "nested": {"client": {"secret": "default-nested-secret"}},
            },
            {
                "id": "team",
                "title": "team title",
                "token": "saved-secret",
                "members": ["old-member"],
                "enabled": False,
                "retries": 1,
                "mode": "alpha",
                "nested": {
                    "client": {"secret": "saved-nested-secret", "keep": "unchanged"}
                },
                "unrelated_instance_setting": "keep",
            },
        ],
    }


def test_set_enabled_preserves_existing_single_instance_shape() -> None:
    plugin = ChannelPlugin(
        name="enabled_contract",
        display_name="Enabled Contract",
        runtime=f"{__name__}:NoRuntime",
        setup=ChannelSetupSpec(fields={"token": ChannelFieldSpec(kind="secret")}),
    )

    updated = channel_set_config_enabled(plugin, {"homeserver": "keep"}, True)
    revision = channel_instance_revision(updated)

    assert revision is not None
    assert updated == {
        "homeserver": "keep",
        "enabled": True,
        CHANNEL_INSTANCE_REVISION_FIELD: revision,
    }


def test_set_enabled_changes_only_the_targeted_multi_instance() -> None:
    updater = _RecordingUpdater()
    plugin = _plugin(
        updater,
        ChannelSetupSpec(fields={"token": ChannelFieldSpec(kind="secret")}),
    )
    section = _section()

    updated = channel_set_config_enabled(plugin, section, True, instance_id="team")

    team = next(instance for instance in updated["instances"] if instance["id"] == "team")
    revision = channel_instance_revision(team)

    assert revision is not None
    assert len(updater.calls) == 1
    assert updater.calls[0][0] == "team"
    assert next(instance for instance in updated["instances"] if instance["id"] == "default") == (
        section["instances"][0]
    )
    assert team == {
        **section["instances"][1],
        "enabled": True,
        CHANNEL_INSTANCE_REVISION_FIELD: revision,
    }


def test_configure_and_enable_rotate_the_internal_instance_revision_in_one_update() -> None:
    """Every instance mutation invalidates its marker without a second management write."""
    updater = _RecordingUpdater()
    plugin = _plugin(
        updater,
        ChannelSetupSpec(
            fields={
                "title": ChannelFieldSpec(),
                "token": ChannelFieldSpec(kind="secret"),
            }
        ),
    )

    configured, _ = channel_configure_instance(
        plugin,
        _section(),
        {"title": "revised"},
        instance_id="team",
    )
    configured_team = next(instance for instance in configured["instances"] if instance["id"] == "team")
    configured_revision = channel_instance_revision(configured_team)

    assert configured_revision is not None
    assert len(updater.calls) == 1
    assert updater.calls[0][0] == "team"
    assert updater.calls[0][1]["title"] == "revised"
    assert updater.calls[0][1][CHANNEL_INSTANCE_REVISION_FIELD] == configured_revision

    enabled = channel_set_config_enabled(plugin, configured, True, instance_id="team")
    enabled_team = next(instance for instance in enabled["instances"] if instance["id"] == "team")
    enabled_revision = channel_instance_revision(enabled_team)

    assert enabled_revision is not None
    assert enabled_revision != configured_revision
    assert len(updater.calls) == 2
    assert updater.calls[1][0] == "team"
    assert updater.calls[1][1]["enabled"] is True
    assert updater.calls[1][1][CHANNEL_INSTANCE_REVISION_FIELD] == enabled_revision


def test_configure_coerces_every_field_kind_for_exact_named_instance() -> None:
    updater = _RecordingUpdater()
    plugin = _plugin(
        updater,
        ChannelSetupSpec(
            fields={
                "title": ChannelFieldSpec(),
                "token": ChannelFieldSpec(kind="secret"),
                "members": ChannelFieldSpec(kind="list"),
                "enabled": ChannelFieldSpec(kind="bool"),
                "retries": ChannelFieldSpec(kind="int"),
                "mode": ChannelFieldSpec(
                    kind="enum", choices=frozenset({"alpha", "beta"})
                ),
                "nested.client.secret": ChannelFieldSpec(kind="secret"),
            }
        ),
    )
    section = _section()
    raw_values = {
        "channels.configure_contract.title": "  updated team  ",
        "token": "  replacement-secret  ",
        "channels.configure_contract.members": " alice, bob , ",
        "enabled": "on",
        "channels.configure_contract.retries": "42",
        "mode": " beta ",
        "nested.client.secret": " nested-replacement ",
    }

    updated, saved = channel_configure_instance(
        plugin,
        section,
        raw_values,
        instance_id="team",
    )

    assert saved == tuple(raw_values)
    assert len(updater.calls) == 1
    assert updater.calls[0][0] == "team"
    team = next(instance for instance in updated["instances"] if instance["id"] == "team")
    revision = channel_instance_revision(team)

    assert revision is not None
    assert team == {
        "id": "team",
        "title": "updated team",
        "token": "replacement-secret",
        "members": ["alice", "bob"],
        "enabled": True,
        "retries": 42,
        "mode": "beta",
        "nested": {
            "client": {"secret": "nested-replacement", "keep": "unchanged"}
        },
        "unrelated_instance_setting": "keep",
        CHANNEL_INSTANCE_REVISION_FIELD: revision,
    }
    assert next(instance for instance in updated["instances"] if instance["id"] == "default") == (
        section["instances"][0]
    )
    assert updated["preserved_channel_setting"] == "keep"


@pytest.mark.parametrize(
    "raw_members",
    [
        pytest.param([" alice ", 3, " "], id="list-of-scalars"),
        pytest.param((" alice ", 3, " "), id="tuple-of-scalars"),
    ],
)
def test_configure_accepts_scalar_list_sequences(raw_members: object) -> None:
    updater = _RecordingUpdater()
    plugin = _plugin(
        updater,
        ChannelSetupSpec(fields={"members": ChannelFieldSpec(kind="list")}),
    )

    updated, saved = channel_configure_instance(
        plugin,
        _section(),
        {"members": raw_members},
        instance_id="team",
    )

    assert saved == ("members",)
    assert len(updater.calls) == 1
    assert next(instance for instance in updated["instances"] if instance["id"] == "team")[
        "members"
    ] == ["alice", "3"]


def test_configure_blank_secrets_preserve_existing_values() -> None:
    updater = _RecordingUpdater()
    plugin = _plugin(
        updater,
        ChannelSetupSpec(
            fields={
                "title": ChannelFieldSpec(),
                "token": ChannelFieldSpec(kind="secret"),
                "nested.client.secret": ChannelFieldSpec(kind="secret"),
            }
        ),
    )

    updated, saved = channel_configure_instance(
        plugin,
        _section(),
        {
            "title": "changed",
            "channels.configure_contract.token": "   ",
            "nested.client.secret": "",
        },
        instance_id="team",
    )

    assert saved == ("title",)
    assert len(updater.calls) == 1
    team = next(instance for instance in updated["instances"] if instance["id"] == "team")
    assert team["title"] == "changed"
    assert team["token"] == "saved-secret"
    assert team["nested"]["client"]["secret"] == "saved-nested-secret"


@pytest.mark.parametrize(
    ("setup", "raw_values"),
    [
        pytest.param(None, {"title": "updated"}, id="absent-setup"),
        pytest.param(
            ChannelSetupSpec(fields={"title": ChannelFieldSpec()}),
            {"channels.other.title": "updated"},
            id="wrong-channel-prefix",
        ),
        pytest.param(
            ChannelSetupSpec(fields={"title": ChannelFieldSpec()}),
            {"unknown": "updated"},
            id="unknown-bare-field",
        ),
        pytest.param(
            ChannelSetupSpec(
                fields={"read_only": ChannelFieldSpec(writable=False)}
            ),
            {"read_only": "updated"},
            id="read-only-field",
        ),
        pytest.param(
            ChannelSetupSpec(
                fields={f"field_{index}": ChannelFieldSpec() for index in range(65)}
            ),
            {f"field_{index}": "updated" for index in range(65)},
            id="more-than-64-values",
        ),
        pytest.param(
            ChannelSetupSpec(fields={"title": ChannelFieldSpec()}),
            ["title"],
            id="non-mapping-values",
        ),
        pytest.param(
            ChannelSetupSpec(fields={"members": ChannelFieldSpec(kind="list")}),
            {"members": [{"not": "a scalar"}]},
            id="non-scalar-list-value",
        ),
    ],
)
def test_configure_rejects_invalid_input_before_updating(
    setup: ChannelSetupSpec | None,
    raw_values: object,
) -> None:
    updater = _RecordingUpdater()
    plugin = _plugin(updater, setup)

    with pytest.raises(ValueError):
        channel_configure_instance(plugin, _section(), raw_values)  # type: ignore[arg-type]

    assert updater.calls == []
