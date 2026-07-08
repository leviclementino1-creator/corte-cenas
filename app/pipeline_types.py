"""Lightweight types shared between the pipeline and the UI.

Importing this module MUST be cheap — no torch, no cv2, no open_clip.
The main window imports from here so the app can open in ~2s instead of
paying the ~10s tax of loading torch just to render UI. The heavy
`Pipeline` class stays in `pipeline.py` and is only imported when the
user actually starts an analysis (from `ui/worker.py` at click time).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class AIMode(str, Enum):
    """How the AI recognition path should look at each shot."""
    FULL = "full"       # send the middle keyframe as a whole to the model
    HYBRID = "hybrid"   # send only YOLO face crops


class InsufficientRefsError(RuntimeError):
    """No character ended up with usable reference images (dead APIs, brand
    new season...). Carries the refs folder so the UI can offer to open it —
    the user can drop face images in per-character subfolders and re-run."""

    def __init__(self, message: str, refs_dir: str) -> None:
        super().__init__(message)
        self.refs_dir = refs_dir


class PipelineCancelled(BaseException):
    """Raised from inside the progress callback when the user hits Cancelar.
    The pipeline reports progress at every loop iteration, so raising here
    unwinds the whole run at the next stage/shot boundary without every loop
    needing its own cancel check.

    Subclasses BaseException (like KeyboardInterrupt) on purpose: the raise
    happens deep inside loops that are wrapped in generic `except Exception`
    blocks — ours and third-party — and none of them may swallow a cancel."""


ProgressCb = Callable[[str, float, str], None]
"""(stage_id, fraction_0_to_1, message) — fraction may be -1 when indeterminate."""


STAGES = [
    ("parse", "Lendo arquivo"),
    ("detect_shots", "Detectando shots"),
    ("cut_shots", "Cortando clipes"),
    ("fetch_characters", "Buscando personagens"),
    ("download_refs", "Baixando referências"),
    ("embed_refs", "Gerando embeddings das referências"),
    ("analyze_shots", "Analisando shots"),
    ("ai_review", "Revisão IA dos duvidosos"),
    ("organize", "Organizando resultados"),
]


@dataclass
class PipelineResult:
    episode_root: Path
    total_shots: int
    total_characters: int
    identified_characters: list[str]
    pair_counts: dict[str, int]
    anime_title: str
    season: int
    episode: int
    episode_id: int
    # Set when the run proceeded with a skeleton crew (1-2 characters with
    # usable refs while others were skipped) — the UI turns it into a dialog
    # offering the refs folder. 3+ usable characters = no nagging.
    low_refs_warning: str | None = None
    refs_dir: str | None = None
