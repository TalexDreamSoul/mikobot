"""Private-vault media relocation for inbound channel attachments."""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from nanobot.config.paths import get_media_dir, get_runtime_subdir


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def relocate_media_to_vault(
    paths: list[str], *, owner_user_id: str, vault_id: str
) -> list[str]:
    """Move channel-managed media into the owner's private vault.

    Only files created below Nanobot's managed media root are eligible.  Caller
    supplied paths are ignored rather than becoming a read primitive.
    """
    media_root = get_media_dir().resolve(strict=False)
    destination_root = (
        get_runtime_subdir("vaults") / owner_user_id / vault_id / "media"
    )
    destination_root.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        destination_root.chmod(0o700)

    relocated: list[str] = []
    for raw_path in paths:
        source = Path(raw_path)
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            continue
        if not _is_under(resolved, media_root) or not resolved.is_file():
            continue
        filename = f"{uuid.uuid4().hex}-{resolved.name}"
        destination = destination_root / filename
        try:
            shutil.move(str(resolved), destination)
            os.chmod(destination, 0o600)
        except OSError:
            continue
        relocated.append(str(destination))
    return relocated
