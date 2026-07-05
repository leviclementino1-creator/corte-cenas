"""Locate the FFmpeg / FFprobe binaries.

Prefers a bundled copy shipped with the installer (in a `bin/` sibling of
the app's exe or module root). Falls back to `ffmpeg` / `ffprobe` on PATH
so source-only setups still work.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def _bundle_root_candidates() -> list[Path]:
    """Directories that MIGHT contain a shipped `bin/ffmpeg.exe`."""
    out: list[Path] = []
    # PyInstaller onedir: sys._MEIPASS is _internal/, exe is one level up.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass)
        out.append(p)              # _internal/
        out.append(p.parent)       # exe folder
    # Source layout: app/ffmpeg_locate.py -> project root is parent.parent.
    here = Path(__file__).resolve().parent
    out.append(here)               # app/
    out.append(here.parent)        # project root
    return out


def _find(exe_name: str) -> str:
    """Return an absolute path to `exe_name` (.exe on Windows) if we ship
    one, else the bare name so subprocess resolves it via PATH."""
    if not exe_name.lower().endswith(".exe") and sys.platform == "win32":
        exe_name = exe_name + ".exe"
    for root in _bundle_root_candidates():
        candidate = root / "bin" / exe_name
        if candidate.exists():
            return str(candidate)
    # Fall back to PATH resolution
    hit = shutil.which(exe_name)
    if hit:
        return hit
    # Last resort: return the name and let subprocess raise a clear error.
    return exe_name


def ffmpeg_binary() -> str:
    return _find("ffmpeg")


def ffprobe_binary() -> str:
    return _find("ffprobe")


def run_ffmpeg_hidden(stream) -> None:
    """Run an ffmpeg-python stream without popping a console window.

    `stream.run(cmd=..., quiet=True)` uses subprocess.Popen but has no way
    to pass creationflags, so on Windows a CMD flashes for every shot cut.
    We compile the args ourselves and Popen with CREATE_NO_WINDOW.
    Raises ffmpeg.Error on non-zero exit (drop-in replacement for .run())."""
    import subprocess
    import ffmpeg  # for ffmpeg.Error

    args = stream.compile(cmd=ffmpeg_binary(), overwrite_output=True)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        args,
        capture_output=True,
        creationflags=creationflags,
    )
    if proc.returncode != 0:
        raise ffmpeg.Error("ffmpeg", proc.stdout, proc.stderr)


def is_bundled() -> bool:
    """True iff we found a shipped ffmpeg (not just PATH). Used by the
    startup check so we don't nag users we can already serve."""
    for root in _bundle_root_candidates():
        if (root / "bin" / "ffmpeg.exe").exists():
            return True
    return False
