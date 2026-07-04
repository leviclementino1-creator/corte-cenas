from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

# shutil is also used by clear_grouping


_INVALID = re.compile(r'[<>:"/\\|?*]')


def sanitize(name: str) -> str:
    name = _INVALID.sub("_", name).strip()
    return name[:120] or "unknown"


def clear_grouping(episode_root: Path) -> None:
    """Remove old by_character / by_pair folders so a fresh run doesn't
    leave stale hardlinks from previous (possibly wrong) classifications.
    """
    for sub in ("by_character", "by_pair"):
        d = episode_root / sub
        if not d.exists():
            continue
        shutil.rmtree(d, ignore_errors=True)


def link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink same-volume, fall back to copy otherwise."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
        return
    except OSError:
        pass
    shutil.copy2(src, dst)


def organize_by_character(
    shot_file: Path,
    episode_root: Path,
    characters: list[str],
) -> None:
    stem = shot_file.name
    for name in characters:
        folder = episode_root / "by_character" / sanitize(name)
        link_or_copy(shot_file, folder / stem)


def organize_by_pair(
    shot_file: Path,
    episode_root: Path,
    characters: list[str],
) -> None:
    if len(characters) < 2:
        return
    stem = shot_file.name
    names = sorted(set(characters))
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pair = f"{sanitize(names[i])}+{sanitize(names[j])}"
            folder = episode_root / "by_pair" / pair
            link_or_copy(shot_file, folder / stem)
