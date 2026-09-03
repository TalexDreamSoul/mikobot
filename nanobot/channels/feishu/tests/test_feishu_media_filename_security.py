from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.channels.feishu import runtime as feishu_module
from nanobot.channels.feishu.config import FeishuConfig
from nanobot.channels.feishu.runtime import FeishuChannel


def _bare_channel(instance_id: str = "default") -> FeishuChannel:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.config = FeishuConfig(instance_id=instance_id)
    channel.logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    return channel


@pytest.mark.asyncio
async def test_feishu_downloaded_media_filename_cannot_escape_media_dir(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    outside = tmp_path / "escaped.txt"

    monkeypatch.setattr(feishu_module, "get_media_dir", lambda _channel: media_dir)

    channel = _bare_channel()

    def fake_download(_message_id, _file_key, _resource_type):
        return b"owned", "../escaped.txt"

    channel._download_file_sync = fake_download

    path_str, content = await channel._download_and_save_media(
        "file", {"file_key": "fk_123"}, "msg_123"
    )

    saved_path = Path(path_str)
    assert not outside.exists()
    assert saved_path.parent == media_dir
    assert saved_path.name.endswith("escaped.txt")
    assert saved_path.read_bytes() == b"owned"
    assert content == f"[file: {saved_path}]"


@pytest.mark.asyncio
async def test_feishu_stored_media_filename_is_not_the_sender_basename(monkeypatch, tmp_path):
    """The sender-supplied basename alone must not determine the stored path."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(feishu_module, "get_media_dir", lambda _channel: media_dir)

    channel = _bare_channel()
    channel._download_file_sync = lambda *_args: (b"payload", "q3-forecast.xlsx")

    path_str, _ = await channel._download_and_save_media("file", {"file_key": "fk"}, "msg")

    saved_path = Path(path_str)
    assert saved_path.name != "q3-forecast.xlsx"
    assert saved_path.name.endswith("-q3-forecast.xlsx")
    assert not (media_dir / "q3-forecast.xlsx").exists()


@pytest.mark.asyncio
async def test_feishu_repeated_upload_of_one_name_never_overwrites(monkeypatch, tmp_path):
    """Two uploads of the same name land on two paths, so neither is clobbered."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(feishu_module, "get_media_dir", lambda _channel: media_dir)

    channel = _bare_channel()

    channel._download_file_sync = lambda *_args: (b"first", "report.pdf")
    first, _ = await channel._download_and_save_media("file", {"file_key": "fk"}, "msg_1")

    channel._download_file_sync = lambda *_args: (b"second", "report.pdf")
    second, _ = await channel._download_and_save_media("file", {"file_key": "fk"}, "msg_2")

    assert first != second
    assert Path(first).read_bytes() == b"first"
    assert Path(second).read_bytes() == b"second"
