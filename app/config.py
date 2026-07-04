from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "CorteCenas"
CONFIG_DIR = Path(user_config_dir(APP_NAME))
CONFIG_FILE = CONFIG_DIR / "config.json"

# Only these fields are written to the JSON between sessions. Matching
# parameters live in this file and are controlled by the code — editing
# `config.py` applies on the next run without needing to touch the JSON.
_PERSISTED_FIELDS = (
    "output_dir",
    "last_anime",
    "last_season",
    "last_episode",
    "default_threshold",
    "argmax_margin",
    "min_shots_per_character",
    "face_crop_padding",
    "credit_edge_threshold",
    # skip_credit_shots: intentionally NOT persisted — the heuristic is
    # fragile and we keep it OFF by default.
    "use_danbooru",
    "navyai_api_key",
    "navyai_base_url",
    "navyai_model",
    "gemini_api_key",
    "gemini_model",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "Output"
DEFAULT_CACHE = PROJECT_ROOT / "cache"
DEFAULT_MODELS = PROJECT_ROOT / "models"


@dataclass
class Config:
    output_dir: str = str(DEFAULT_OUTPUT)
    cache_dir: str = str(DEFAULT_CACHE)
    models_dir: str = str(DEFAULT_MODELS)

    # Shot detection
    scene_threshold: float = 27.0
    min_shot_seconds: float = 0.6

    # Cutting
    reencode_shots: bool = True

    # Keyframes per shot used for recognition
    keyframes_per_shot: int = 3

    # Reference images per character to download
    references_per_character: int = 8
    # With franchise pooling we easily get 60-80 characters for long series;
    # the min_references filter naturally drops the weakly-covered ones.
    max_characters_per_anime: int = 80
    # Danbooru has wide coverage but often returns fan art with multiple
    # characters in the same image → contaminates centroids. Off by default.
    use_danbooru: bool = False

    # Matching
    default_threshold: float = 0.80
    # When True, shots with zero detected faces get no character assignment
    # (instead of falling back to whole-keyframe matching, which produces
    # false positives on scenes that merely share color/composition with
    # a character's refs — e.g. food shots matching an orange-clothed char).
    # With YOLO anime-face catching ~58% of shots, this tradeoff favours
    # precision over recall.
    face_exclusive_when_detected: bool = True
    min_references_per_character: int = 2   # chars with fewer refs are ignored
    argmax_margin: float = 0.03             # best must beat 2nd by this
    min_shots_per_character: int = 4        # post-hoc: chars under this get dropped
    min_keyframe_votes: int = 2             # char must be detected in >=N keyframes
    face_crop_padding: float = 0.25         # around detected face; more context helps CLIP
    # AI hybrid mode crops get WIDER padding so hair/headband is fully
    # visible — that's often the only thing telling apart similar-styled
    # characters (Chrome vs Senku, Kohaku vs Ruri, etc.).
    face_crop_padding_ai: float = 0.55

    # Credit/text-overlay shot filter (OP/ED, credits rolls).
    # OFF by default — the heuristic over-fires on rich art (witch hats,
    # complex labs, crowded scenes). Use manual OP/ED time skip in the UI
    # for reliable credit exclusion. Users can still flip this on per run.
    skip_credit_shots: bool = False
    credit_edge_threshold: float = 0.55
    credit_min_keyframes: int = 2
    # ViT-L/14: ~890MB download on first run, much better anime discrimination
    # than ViT-B/32. Fine on a 5080 (≈1.5GB VRAM, seconds per episode).
    clip_model: str = "ViT-L-14"
    clip_pretrained: str = "openai"

    # GPU
    use_cuda: bool = True  # falls back to CPU automatically if unavailable

    # UI
    language: str = "pt"

    last_anime: str = ""
    last_season: int = 1
    last_episode: int = 1

    # AI review — primary provider (NavyAI, OpenAI-compatible gateway).
    # Optional; if no key is set, the AI features are disabled.
    navyai_api_key: str = ""
    navyai_base_url: str = "https://api.navy/v1"
    navyai_model: str = "gemini-2.0-flash"

    # AI review — fallback provider (Gemini native, hit directly via Google's
    # OpenAI-compatible endpoint). Kicks in automatically if NavyAI returns
    # 5xx / rate-limits / errors. Useful with the free tier of both — when one
    # exhausts, the other picks up the slack.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k in _PERSISTED_FIELDS:
                    if k in data:
                        setattr(cfg, k, data[k])
            except Exception:
                pass
        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {k: getattr(self, k) for k in _PERSISTED_FIELDS}
        CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir)

    @property
    def models_path(self) -> Path:
        return Path(self.models_dir)

    def ensure_dirs(self) -> None:
        for p in (self.output_path, self.cache_path, self.models_path):
            p.mkdir(parents=True, exist_ok=True)
        (self.cache_path / "anime_db").mkdir(parents=True, exist_ok=True)
