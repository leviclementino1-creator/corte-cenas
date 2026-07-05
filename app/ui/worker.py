"""QThread workers around the pipeline / harvest / reframe entry points.

Imports here MUST stay light. Heavy modules (`torch`, `open_clip`, `cv2`,
`ultralytics`) are pulled in inside each `run()` — that's the moment the
user actually clicked Analisar / Harvest / Reframe and is prepared to
wait. Importing them at module scope would tack ~10 seconds onto every
cold app start just so the UI can render.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Config
from ..pipeline_types import AIMode, PipelineResult
from ..video_ingest import EpisodeInfo


class PipelineWorker(QObject):
    stage = Signal(str, float, str)          # (stage_id, fraction, message)
    finished = Signal(object)                 # PipelineResult
    failed = Signal(str)

    def __init__(
        self,
        config: Config,
        info: EpisodeInfo,
        use_ai_recognition: bool = False,
        ai_mode: AIMode = AIMode.FULL,
    ) -> None:
        super().__init__()
        self.config = config
        self.info = info
        self.use_ai_recognition = use_ai_recognition
        self.ai_mode = ai_mode

    def run(self) -> None:
        try:
            from ..pipeline import Pipeline  # heavy — deferred to click-time
            pipeline = Pipeline(self.config)
            result = pipeline.run(
                self.info,
                on_progress=self._emit,
                use_ai_recognition=self.use_ai_recognition,
                ai_mode=self.ai_mode,
            )
            self.finished.emit(result)
        except Exception as e:
            tb = traceback.format_exc()
            print("\n=== Pipeline falhou ===", file=sys.stderr)
            print(tb, file=sys.stderr, flush=True)
            self.failed.emit(f"{e}\n\n{tb}")

    def _emit(self, stage: str, frac: float, msg: str) -> None:
        self.stage.emit(stage, float(frac), str(msg))


class RefsPreviewWorker(QObject):
    """Runs ONLY the ref-fetching portion of the pipeline:
    AniList resolve → Jikan + Danbooru fetch URLs → download images.
    No CLIP, no shot detection, no classification.
    """

    status = Signal(str)
    finished = Signal(dict)   # {"folder": str, "per_char": {name: count}, "title": str}
    failed = Signal(str)

    def __init__(self, config: Config, anime_name: str, season: int = 1) -> None:
        super().__init__()
        self.config = config
        self.anime_name = anime_name
        self.season = season

    def run(self) -> None:
        try:
            from ..providers.anime_provider import AnimeProvider
            from ..references.reference_store import ReferenceStore

            self.config.ensure_dirs()
            provider = AnimeProvider(self.config.cache_path)
            try:
                bundle = provider.resolve(
                    self.anime_name,
                    max_characters=self.config.max_characters_per_anime,
                    images_per_character=self.config.references_per_character,
                    on_status=lambda m: self.status.emit(m),
                    use_danbooru=self.config.use_danbooru,
                    season=self.season,
                )
            finally:
                provider.close()

            self.status.emit("Baixando imagens...")
            store = ReferenceStore(self.config.cache_path)
            if bundle.franchise_root_id:
                cache_id = f"al{bundle.franchise_root_id}"
            elif bundle.anilist_id:
                cache_id = f"al{bundle.anilist_id}"
            else:
                cache_id = f"mal{bundle.mal_id}"
            refs = store.ensure_references(
                cache_id, bundle, on_status=lambda m: self.status.emit(m)
            )
            per_char = {name: len(paths) for name, paths in refs.items()}
            folder = str(store.anime_dir(cache_id) / "characters")
            self.finished.emit(
                {"folder": folder, "per_char": per_char, "title": bundle.title}
            )
        except Exception as e:
            tb = traceback.format_exc()
            print("\n=== Refs preview falhou ===", file=sys.stderr)
            print(tb, file=sys.stderr, flush=True)
            self.failed.emit(f"{e}\n\n{tb}")


class HarvestWorker(QObject):
    """Extracts high-confidence face crops from the current episode as
    additional references for each character, saving into their ref folder.
    """

    progress = Signal(str, int, int)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        config: Config,
        episode_id: int,
        episode_root: Path,
        anime_cache_id: str,
        conf_threshold: float = 0.90,
        max_new_refs_per_char: int = 3,
    ) -> None:
        super().__init__()
        self.config = config
        self.episode_id = episode_id
        self.episode_root = Path(episode_root)
        self.anime_cache_id = anime_cache_id
        self.conf_threshold = conf_threshold
        self.max_new_refs = max_new_refs_per_char

    def run(self) -> None:
        try:
            from ..harvest import harvest_all_characters
            from ..matching.embedding_engine import EmbeddingEngine
            from ..matching.face_detector import AnimeFaceDetector
            from ..references.reference_store import ReferenceStore
            from ..storage.db import Database

            print(f"[harvest] Iniciando para episode_id={self.episode_id}", flush=True)
            face_det = AnimeFaceDetector()
            engine = EmbeddingEngine(
                model_name=self.config.clip_model,
                pretrained=self.config.clip_pretrained,
                use_cuda=self.config.use_cuda,
            )
            db = Database(self.config.cache_path / "index.db")
            store = ReferenceStore(self.config.cache_path)

            results = harvest_all_characters(
                self.episode_root,
                self.episode_id,
                self.anime_cache_id,
                db,
                store,
                face_det,
                engine,
                conf_threshold=self.conf_threshold,
                max_new_refs_per_char=self.max_new_refs,
                on_progress=lambda name, d, t: self.progress.emit(name, d, t),
            )
            self.finished.emit(results)
        except Exception as e:
            tb = traceback.format_exc()
            print("\n=== Harvest falhou ===", file=sys.stderr)
            print(tb, file=sys.stderr, flush=True)
            self.failed.emit(f"{e}\n\n{tb}")


class ReframeWorker(QObject):
    """Reframes all shots of a character to vertical 9:16 centered on the
    detected face (with motion-energy fallback when no face is found).
    """

    progress = Signal(int, int)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        config: Config,
        episode_root: Path,
        character_id: int,
        character_name: str,
        shot_rows: list[dict],
        target_w: int = 1080,
        target_h: int = 1920,
    ) -> None:
        super().__init__()
        self.config = config
        self.episode_root = Path(episode_root)
        self.character_id = character_id
        self.character_name = character_name
        self.shot_rows = shot_rows
        self._target_w = target_w
        self._target_h = target_h

    def run(self) -> None:
        try:
            from ..matching.face_detector import AnimeFaceDetector, ensure_cascade
            from ..reframe import ReframeTarget, reframe_character

            target = ReframeTarget(width=self._target_w, height=self._target_h)
            face_det = AnimeFaceDetector(ensure_cascade(self.config.models_path))
            folder, ok, total = reframe_character(
                self.episode_root,
                self.character_name,
                self.shot_rows,
                face_det,
                target,
                on_progress=lambda d, t: self.progress.emit(d, t),
            )
            self.finished.emit(
                {"folder": str(folder), "ok": ok, "total": total, "name": self.character_name}
            )
        except Exception as e:
            tb = traceback.format_exc()
            print("\n=== Reframe falhou ===", file=sys.stderr)
            print(tb, file=sys.stderr, flush=True)
            self.failed.emit(f"{e}\n\n{tb}")


def start_pipeline(parent: QObject, config: Config, info: EpisodeInfo) -> tuple[QThread, PipelineWorker]:
    thread = QThread(parent)
    worker = PipelineWorker(config, info)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    return thread, worker
