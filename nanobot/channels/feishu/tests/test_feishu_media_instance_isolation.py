"""Per-instance media isolation for Feishu assistant instances.

Before this, every Feishu instance wrote to ``~/.nanobot/media/feishu`` under
the sender-supplied basename, so an upload to one tenant's instance replaced a
same-named attachment held by another tenant's instance.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.channels.feishu.config import FeishuConfig
from nanobot.channels.feishu.runtime import FeishuChannel

SHARED_NAME = "q3-forecast.xlsx"


@pytest.fixture
def media_root(monkeypatch, tmp_path) -> Path:
    """Point the real ``get_media_dir`` at a temporary data dir."""
    monkeypatch.setattr(
        "nanobot.config.paths.get_config_path", lambda: tmp_path / "config.json"
    )
    return tmp_path / "media"


def _instance(instance_id: str, payload: bytes) -> FeishuChannel:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.config = FeishuConfig(instance_id=instance_id)
    channel.logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    channel._download_file_sync = lambda *_args: (payload, SHARED_NAME)
    return channel


async def _receive(channel: FeishuChannel) -> Path:
    path_str, _ = await channel._download_and_save_media(
        "file", {"file_key": "fk_shared"}, "om_shared"
    )
    assert path_str is not None
    return Path(path_str)


@pytest.mark.asyncio
async def test_two_instances_receiving_one_filename_do_not_collide(media_root):
    tenant_a = await _receive(_instance("default", b"tenant-a-forecast"))
    tenant_b = await _receive(_instance("tenant-b", b"tenant-b-forecast"))

    assert tenant_a != tenant_b
    assert tenant_a.parent == media_root / "feishu"
    assert tenant_b.parent == media_root / "feishu.tenant-b"
    # Neither upload landed on the other's bytes.
    assert tenant_a.read_bytes() == b"tenant-a-forecast"
    assert tenant_b.read_bytes() == b"tenant-b-forecast"


@pytest.mark.asyncio
async def test_one_instance_cannot_derive_another_instances_stored_path(media_root):
    tenant_a = await _receive(_instance("tenant-a", b"tenant-a-forecast"))
    tenant_b_dir = media_root / "feishu.tenant-b"

    # Knowing the filename it sent and its own directory tells tenant B nothing
    # about where tenant A's copy lives.
    assert not (tenant_b_dir / SHARED_NAME).exists()
    assert not (tenant_a.parent / SHARED_NAME).exists()
    assert not (tenant_b_dir / tenant_a.name).exists()
    assert tenant_a.name.endswith(f"-{SHARED_NAME}")
    assert tenant_a.name != SHARED_NAME


@pytest.mark.asyncio
async def test_default_instance_keeps_the_pre_existing_media_directory(media_root):
    """Media written under the old shared layout stays where the agent left it."""
    legacy_dir = media_root / "feishu"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "already-downloaded.pdf"
    legacy_file.write_bytes(b"legacy")

    saved = await _receive(_instance("default", b"new-upload"))

    assert saved.parent == legacy_dir
    assert legacy_file.read_bytes() == b"legacy"
