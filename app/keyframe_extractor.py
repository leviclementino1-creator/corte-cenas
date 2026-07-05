from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import cv2
import ffmpeg

from .ffmpeg_locate import run_ffmpeg_hidden
from .shot_detection import ShotBounds


def cut_shot(
    video_path: str | Path,
    shot: ShotBounds,
    out_file: Path,
    reencode: bool = True,
) -> None:
    """Extract a shot to an mp4 file. Re-encode for frame accuracy, or stream-copy for speed.

    Overwrites any existing file at `out_file`.
    """
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        try:
            out_file.unlink()
        except OSError:
            pass

    if reencode:
        stream = ffmpeg.input(str(video_path), ss=shot.start, to=shot.end).output(
            str(out_file),
            vcodec="libx264",
            preset="ultrafast",
            crf=20,
            acodec="aac",
            format="mp4",
            movflags="+faststart",
            loglevel="error",
        )
    else:
        stream = ffmpeg.input(str(video_path), ss=shot.start, to=shot.end).output(
            str(out_file),
            c="copy",
            format="mp4",
            avoid_negative_ts="make_zero",
            loglevel="error",
        )
    run_ffmpeg_hidden(stream)


def extract_keyframes(
    video_path: str | Path,
    shot: ShotBounds,
    out_dir: Path,
    n_frames: int = 3,
) -> list[Path]:
    """Sample N frames uniformly across the shot and save as JPGs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    paths: list[Path] = []

    if n_frames <= 1:
        offsets = [0.5]
    else:
        offsets = [(i + 1) / (n_frames + 1) for i in range(n_frames)]

    for k, off in enumerate(offsets):
        t = shot.start + shot.duration * off
        frame_idx = int(t * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            continue
        out = out_dir / f"{shot.idx:04d}_{k}.jpg"
        cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        paths.append(out)

    cap.release()
    return paths


def cut_all_shots(
    video_path: str | Path,
    shots: list[ShotBounds],
    shots_dir: Path,
    keyframes_dir: Path,
    keyframes_per_shot: int,
    reencode: bool,
    on_progress: Callable[[int, int, int], None] | None = None,
    skip_existing: bool = True,
) -> list[tuple[ShotBounds, Path, list[Path]]]:
    """Cut shots and extract keyframes.

    If `skip_existing` is True, shots whose .mp4 is already on disk (non-empty)
    are not re-encoded, and keyframes already present are not re-extracted.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)
    keyframes_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[ShotBounds, Path, list[Path]]] = []
    total = len(shots)
    skipped = 0
    for i, shot in enumerate(shots):
        out_file = shots_dir / f"{shot.idx:04d}.mp4"
        expected_kfs = [keyframes_dir / f"{shot.idx:04d}_{k}.jpg" for k in range(keyframes_per_shot)]

        have_cut = out_file.exists() and out_file.stat().st_size > 0
        have_kfs = all(p.exists() and p.stat().st_size > 0 for p in expected_kfs)

        if skip_existing and have_cut:
            pass
        else:
            try:
                cut_shot(video_path, shot, out_file, reencode=reencode)
            except ffmpeg.Error:
                continue

        if skip_existing and have_kfs:
            kfs = expected_kfs
        else:
            kfs = extract_keyframes(video_path, shot, keyframes_dir, n_frames=keyframes_per_shot)

        if skip_existing and have_cut and have_kfs:
            skipped += 1

        results.append((shot, out_file, kfs))
        if on_progress:
            on_progress(i + 1, total, skipped)
    return results
