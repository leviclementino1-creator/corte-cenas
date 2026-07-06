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


class PipelineCancelled(Exception):
    """Raised from inside the progress callback when the user hits Cancelar.
    The pipeline reports progress at every loop iteration, so raising here
    unwinds the whole run at the next stage/shot boundary without every loop
    needing its own cancel check."""


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
