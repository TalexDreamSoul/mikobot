"""Member attachment boundaries for project-scoped tool calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.security.private_media import (
    resolve_user_private_media_file,
    user_private_root,
)


def _runtime_users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: runtime_root / name,
    )
    return runtime_root / "users"


def test_member_attachment_allows_only_its_exact_private_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users_root = _runtime_users_root(tmp_path, monkeypatch)
    attachment = users_root / "member" / "media" / "alpha" / "review.pdf"
    same_member_other_project = users_root / "member" / "media" / "beta" / "beta-secret.pdf"
    other_member = users_root / "other-member" / "media" / "alpha" / "other-secret.pdf"
    global_media = tmp_path / "global-media" / "host-secret.pdf"
    attachment.parent.mkdir(parents=True)
    same_member_other_project.parent.mkdir(parents=True)
    other_member.parent.mkdir(parents=True)
    global_media.parent.mkdir()
    attachment.write_text("member alpha attachment", encoding="utf-8")
    same_member_other_project.write_text("member beta secret", encoding="utf-8")
    other_member.write_text("other member secret", encoding="utf-8")
    global_media.write_text("host global secret", encoding="utf-8")

    allowed = resolve_user_private_media_file(
        attachment,
        owner_user_id="member",
        project_id="alpha",
    )

    assert allowed.read_text(encoding="utf-8") == "member alpha attachment"
    for forbidden in (same_member_other_project, other_member, global_media):
        with pytest.raises(PermissionError):
            resolve_user_private_media_file(
                forbidden,
                owner_user_id="member",
                project_id="alpha",
            )


def test_member_attachment_rejects_a_symlink_to_host_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users_root = _runtime_users_root(tmp_path, monkeypatch)
    host_secret = tmp_path / "host-history" / "history.jsonl"
    attachment_link = users_root / "member" / "media" / "alpha" / "looks-like-an-attachment.txt"
    host_secret.parent.mkdir()
    attachment_link.parent.mkdir(parents=True)
    host_secret.write_text("host-only history", encoding="utf-8")
    try:
        attachment_link.symlink_to(host_secret)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(PermissionError):
        resolve_user_private_media_file(
            attachment_link,
            owner_user_id="member",
            project_id="alpha",
        )


@pytest.mark.parametrize("owner_user_id", ["", "../host", "/host", ".", "..", "member/child"])
def test_private_media_rejects_non_identifier_owner_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_user_id: str,
) -> None:
    _runtime_users_root(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        user_private_root(owner_user_id)
