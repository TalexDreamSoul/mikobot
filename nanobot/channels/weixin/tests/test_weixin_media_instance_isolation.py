"""Per-instance media isolation for WeChat instances.

Before this, every WeChat instance wrote to ``~/.nanobot/media/weixin`` under
the sender-supplied ``file_name``, so an upload to one tenant's instance
replaced a same-named attachment held by another tenant's instance.
"""

import base64
from pathlib import Path

import pytest

from nanobot.bus.queue import MessageBus
from nanobot.channels.weixin.runtime import WeixinChannel, _encrypt_aes_ecb

SHARED_NAME = "q3-forecast.xlsx"
AES_KEY_HEX = "00112233445566778899aabbccddeeff"
AES_KEY_B64 = base64.b64encode(bytes.fromhex(AES_KEY_HEX)).decode()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def get(self, _url: str) -> _FakeResponse:
        return _FakeResponse(self._content)


@pytest.fixture
def media_root(monkeypatch, tmp_path) -> Path:
    """Point the real ``get_media_dir`` at a temporary data dir."""
    monkeypatch.setattr(
        "nanobot.config.paths.get_config_path", lambda: tmp_path / "config.json"
    )
    return tmp_path / "media"


def _instance(instance_id: str, payload: bytes, tmp_path: Path) -> WeixinChannel:
    channel = WeixinChannel(
        {
            "instanceId": instance_id,
            "enabled": True,
            "allowFrom": ["*"],
            "stateDir": str(tmp_path / f"state-{instance_id}"),
        },
        MessageBus(),
    )
    channel._client = _FakeClient(_encrypt_aes_ecb(payload, AES_KEY_B64))
    return channel


async def _receive(channel: WeixinChannel, filename: str = SHARED_NAME) -> Path:
    path_str = await channel._download_media_item(
        {"aeskey": AES_KEY_HEX, "media": {"full_url": "https://cdn.example/blob"}},
        "file",
        filename,
    )
    assert path_str is not None
    return Path(path_str)


@pytest.mark.asyncio
async def test_two_instances_receiving_one_filename_do_not_collide(media_root, tmp_path):
    tenant_a = await _receive(_instance("default", b"tenant-a-forecast", tmp_path))
    tenant_b = await _receive(_instance("tenant-b", b"tenant-b-forecast", tmp_path))

    assert tenant_a != tenant_b
    assert tenant_a.parent == media_root / "weixin"
    assert tenant_b.parent == media_root / "weixin.tenant-b"
    # Neither upload landed on the other's bytes.
    assert tenant_a.read_bytes().startswith(b"tenant-a-forecast")
    assert tenant_b.read_bytes().startswith(b"tenant-b-forecast")


@pytest.mark.asyncio
async def test_one_instance_cannot_derive_another_instances_stored_path(media_root, tmp_path):
    tenant_a = await _receive(_instance("tenant-a", b"tenant-a-forecast", tmp_path))
    tenant_b_dir = media_root / "weixin.tenant-b"

    assert not (tenant_b_dir / SHARED_NAME).exists()
    assert not (tenant_a.parent / SHARED_NAME).exists()
    assert not (tenant_b_dir / tenant_a.name).exists()
    assert tenant_a.name.endswith(f"-{SHARED_NAME}")
    assert tenant_a.name != SHARED_NAME


@pytest.mark.asyncio
async def test_repeated_upload_of_one_name_never_overwrites(media_root, tmp_path):
    channel = _instance("tenant-a", b"first-upload", tmp_path)
    first = await _receive(channel)

    channel._client = _FakeClient(_encrypt_aes_ecb(b"second-upload", AES_KEY_B64))
    second = await _receive(channel)

    assert first != second
    assert first.read_bytes().startswith(b"first-upload")
    assert second.read_bytes().startswith(b"second-upload")


@pytest.mark.asyncio
async def test_downloaded_media_filename_cannot_escape_media_dir(media_root, tmp_path):
    channel = _instance("tenant-a", b"owned", tmp_path)

    saved = await _receive(channel, r"..\..\escaped.txt")

    assert saved.parent == media_root / "weixin.tenant-a"
    assert not (media_root.parent / "escaped.txt").exists()
    assert saved.read_bytes().startswith(b"owned")


@pytest.mark.asyncio
async def test_default_instance_keeps_the_pre_existing_media_directory(media_root, tmp_path):
    """Media written under the old shared layout stays where the agent left it."""
    legacy_dir = media_root / "weixin"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "already-downloaded.pdf"
    legacy_file.write_bytes(b"legacy")

    saved = await _receive(_instance("default", b"new-upload", tmp_path))

    assert saved.parent == legacy_dir
    assert legacy_file.read_bytes() == b"legacy"
