from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class AIMode(str, Enum):
    """How the AI recognition path should look at each shot."""
    FULL = "full"       # send the middle keyframe as a whole to the model
    HYBRID = "hybrid"   # send only YOLO face crops

import cv2
import numpy as np

from .ai_review import NavyAIClient, classify_face_crops, classify_frame
from .config import Config
from .keyframe_extractor import cut_all_shots
from .matching.character_matcher import CharacterEntry, CharacterMatcher, build_centroid
from .matching.cooccurrence import count_pairs
from .matching.credit_detector import is_credits_frame
from .matching.embedding_engine import EmbeddingEngine, from_bytes, to_bytes
from .matching.face_detector import AnimeFaceDetector, ensure_cascade, smart_portrait_crop
from .providers.anime_provider import AnimeProvider
from .references.reference_store import ReferenceStore
from .shot_detection import ShotBounds, detect_shots
from .storage.db import Database
from .storage.metadata_writer import build_shot_payload, write_characters_json, write_shots_json
from .storage.organizer import clear_grouping, organize_by_character, organize_by_pair, sanitize
from .video_ingest import EpisodeInfo


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


def _noop(stage: str, frac: float, msg: str) -> None:
    pass


class Pipeline:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.cache_path / "index.db")

    def run(
        self,
        info: EpisodeInfo,
        on_progress: ProgressCb | None = None,
        use_ai_recognition: bool = False,
        ai_mode: AIMode | str = AIMode.FULL,
    ) -> PipelineResult:
        cb = on_progress or _noop
        cfg = self.cfg

        cb("parse", 1.0, f"{info.anime} {info.slug}")

        # Output layout
        episode_root = cfg.output_path / sanitize(info.anime) / info.slug
        shots_dir = episode_root / "shots"
        keyframes_dir = episode_root / "keyframes"
        metadata_dir = episode_root / "metadata"
        for d in (shots_dir, keyframes_dir, metadata_dir):
            d.mkdir(parents=True, exist_ok=True)

        # 1) Shot detection (cached per episode folder)
        bounds_cache = metadata_dir / "shot_bounds.json"
        shots: list[ShotBounds] | None = None
        if bounds_cache.exists():
            try:
                data = json.loads(bounds_cache.read_text(encoding="utf-8"))
                if (
                    isinstance(data, dict)
                    and data.get("source") == str(info.source)
                    and abs(float(data.get("threshold", -1)) - cfg.scene_threshold) < 1e-6
                ):
                    shots = [
                        ShotBounds(idx=int(s["idx"]), start=float(s["start"]), end=float(s["end"]))
                        for s in data.get("shots", [])
                    ]
            except (json.JSONDecodeError, ValueError, KeyError, OSError) as e:
                print(f"[CorteCenas] Shot bounds cache inválido, recomputando: {e}")
                shots = None

        if shots:
            cb("detect_shots", 1.0, f"{len(shots)} shots (cache)")
        else:
            cb("detect_shots", -1.0, "Analisando mudanças de cena...")
            shots = detect_shots(
                info.source,
                threshold=cfg.scene_threshold,
                min_seconds=cfg.min_shot_seconds,
            )
            bounds_cache.write_text(
                json.dumps(
                    {
                        "source": str(info.source),
                        "threshold": cfg.scene_threshold,
                        "min_seconds": cfg.min_shot_seconds,
                        "shots": [
                            {"idx": s.idx, "start": s.start, "end": s.end} for s in shots
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cb("detect_shots", 1.0, f"{len(shots)} shots detectados")

        # Apply manual head/tail skip (OP/ED by time)
        if shots and (info.skip_head_seconds > 0 or info.skip_tail_seconds > 0):
            total_duration = max(s.end for s in shots)
            tail_cut = total_duration - info.skip_tail_seconds if info.skip_tail_seconds > 0 else total_duration + 1.0
            before = len(shots)
            shots = [
                s for s in shots
                if s.end > info.skip_head_seconds and s.start < tail_cut
            ]
            print(
                f"[CorteCenas] Skip manual: início {info.skip_head_seconds:.0f}s, "
                f"fim {info.skip_tail_seconds:.0f}s → {before - len(shots)} shots ignorados"
            )

        # 2) Cut shots + keyframes
        def cut_cb(done: int, total: int, skipped: int) -> None:
            suffix = f" ({skipped} já em cache)" if skipped else ""
            cb("cut_shots", done / max(total, 1), f"{done}/{total} shots{suffix}")

        cut_results = cut_all_shots(
            info.source,
            shots,
            shots_dir,
            keyframes_dir,
            keyframes_per_shot=cfg.keyframes_per_shot,
            reencode=cfg.reencode_shots,
            on_progress=cut_cb,
        )

        # 3) Anime / characters
        cb("fetch_characters", -1.0, "Consultando AniList + Jikan...")
        provider = AnimeProvider(cfg.cache_path)
        try:
            bundle = provider.resolve(
                info.anime,
                max_characters=cfg.max_characters_per_anime,
                images_per_character=cfg.references_per_character,
                on_status=lambda m: cb("fetch_characters", -1.0, m),
                use_danbooru=cfg.use_danbooru,
                season=info.season,
            )
        finally:
            provider.close()
        cb("fetch_characters", 1.0, f"{len(bundle.characters)} personagens")

        anime_id = self.db.upsert_anime(
            anilist_id=bundle.anilist_id,
            mal_id=bundle.mal_id,
            title=bundle.title,
            title_english=bundle.title_english,
        )
        episode_id = self.db.upsert_episode(anime_id, info.season, info.episode, str(info.source))
        self.db.clear_episode_shots(episode_id)

        for ch in bundle.characters:
            self.db.upsert_character(
                anime_id=anime_id,
                name=ch.name,
                anilist_id=ch.anilist_id,
                mal_id=ch.mal_id,
                role=ch.role,
            )

        # 4) Download references
        cb("download_refs", -1.0, "Baixando imagens de personagens...")
        ref_store = ReferenceStore(cfg.cache_path)
        # Franchise root ID, when present, is the shared cache key for the
        # whole franchise (all seasons share refs).
        if bundle.franchise_root_id:
            cache_id = f"al{bundle.franchise_root_id}"
        elif bundle.anilist_id:
            cache_id = f"al{bundle.anilist_id}"
        else:
            cache_id = f"mal{bundle.mal_id}"
        refs_per_char = ref_store.ensure_references(
            cache_id, bundle, on_status=lambda m: cb("download_refs", -1.0, m)
        )
        cb("download_refs", 1.0, "Refs prontas")

        # === AI-only recognition path (alternative to CLIP) ===
        if use_ai_recognition:
            return self._run_ai_recognition(
                info=info,
                cb=cb,
                anime_id=anime_id,
                episode_id=episode_id,
                episode_root=episode_root,
                metadata_dir=metadata_dir,
                bundle=bundle,
                refs_per_char=refs_per_char,
                cut_results=cut_results,
                ai_mode=AIMode(ai_mode) if not isinstance(ai_mode, AIMode) else ai_mode,
            )

        # 5) Embeddings
        cb("embed_refs", -1.0, "Carregando modelo CLIP...")
        engine = EmbeddingEngine(
            model_name=cfg.clip_model,
            pretrained=cfg.clip_pretrained,
            use_cuda=cfg.use_cuda,
        )
        if engine.on_device_fallback:
            cb("embed_refs", -1.0, engine.on_device_fallback)
            print(f"[CorteCenas] {engine.on_device_fallback}")

        # Face detector is also used for the references so the rep space
        # matches: face crop (ref) vs face crop (query), not whole ref vs
        # face crop. This removes background contamination from centroids.
        face_det = AnimeFaceDetector(ensure_cascade(cfg.models_path))

        db_chars = {c["name"]: c for c in self.db.get_characters_for_anime(anime_id)}
        entries: list[CharacterEntry] = []
        total_chars = len(bundle.characters)
        ref_stats: list[tuple[str, int, int]] = []  # (name, ref_count, face_count)
        skipped_few_refs: list[str] = []
        for i, ch in enumerate(bundle.characters, 1):
            cb("embed_refs", i / total_chars, f"Embeddando: {ch.name}")
            paths = refs_per_char.get(ch.name, [])
            if len(paths) < cfg.min_references_per_character:
                ref_stats.append((ch.name, len(paths), 0))
                skipped_few_refs.append(f"{ch.name}({len(paths)})")
                continue

            # Detect faces in each ref. If a face is found, the face crop is
            # added; if not, a smart center-crop of the portrait (removes
            # white margins) is used. The centroid then averages both kinds,
            # so it captures both face-level features *and* overall silhouette
            # (hair, clothing, body shape) which are what distinguish one
            # anime character from another generically-similar face.
            face_imgs: list = []
            faces_found = 0
            for p in paths:
                img = cv2.imread(str(p))
                if img is None:
                    continue
                crops = face_det.crop_faces(img, pad=cfg.face_crop_padding)
                if crops:
                    face_imgs.extend(crops)
                    faces_found += len(crops)
                else:
                    face_imgs.append(smart_portrait_crop(img))
            ref_stats.append((ch.name, len(paths), faces_found))
            if not face_imgs:
                continue
            embs = engine.embed_images(face_imgs)
            centroid = build_centroid(embs)
            if centroid is None:
                continue
            db_row = db_chars.get(ch.name)
            if not db_row:
                continue
            self.db.set_character_embedding(
                db_row["id"], to_bytes(centroid), reference_count=len(paths)
            )
            entries.append(
                CharacterEntry(
                    id=db_row["id"],
                    name=ch.name,
                    centroid=centroid,
                    threshold=cfg.default_threshold,
                )
            )
        print(
            "[CorteCenas] Refs por personagem (refs/rostos):",
            ", ".join(f"{n}={rc}/{fc}" for n, rc, fc in ref_stats),
        )
        if skipped_few_refs:
            print(f"[CorteCenas] Ignorados (poucas refs): {', '.join(skipped_few_refs)}")
        cb("embed_refs", 1.0, f"{len(entries)} personagens com embedding")

        matcher = CharacterMatcher(entries)

        # 6) Analyze shots — face_det reused from step 5

        per_shot_names: list[list[str]] = []
        shot_db_ids: list[int] = []
        total = len(cut_results)
        shots_with_faces = 0
        credit_shots = 0
        for i, (shot, shot_file, kfs) in enumerate(cut_results, 1):
            cb("analyze_shots", i / max(total, 1), f"Shot {i}/{total}")
            main_kf = kfs[len(kfs) // 2] if kfs else None
            shot_id = self.db.insert_shot(
                episode_id=episode_id,
                idx=shot.idx,
                file=str(shot_file.relative_to(episode_root)),
                keyframe=str(main_kf.relative_to(episode_root)) if main_kf else None,
                start=shot.start,
                end=shot.end,
            )
            shot_db_ids.append(shot_id)

            # Credit / OP / ED detection — skip shots dominated by text overlay.
            if cfg.skip_credit_shots and kfs:
                credit_count = 0
                for kf_path in kfs:
                    img = cv2.imread(str(kf_path))
                    if img is None:
                        continue
                    if is_credits_frame(img, cfg.credit_edge_threshold):
                        credit_count += 1
                if credit_count >= cfg.credit_min_keyframes:
                    credit_shots += 1
                    per_shot_names.append([])
                    continue

            # Per-keyframe face-based assignments. Tracking votes across
            # keyframes lets us filter out single-frame cameos (a character
            # that shows up in only 1 of 3 keyframes is almost always noise
            # or a flash-through, not a real presence).
            per_kf_assigns: list[dict[int, float]] = []
            had_any_faces = False
            for kf_path in kfs:
                img = cv2.imread(str(kf_path))
                if img is None:
                    continue
                crops = face_det.crop_faces(img, pad=cfg.face_crop_padding)
                if not crops:
                    continue
                had_any_faces = True
                embs = engine.embed_images(crops)
                d = dict(matcher.assign_best_per_query(embs, margin=cfg.argmax_margin))
                per_kf_assigns.append(d)

            if had_any_faces:
                shots_with_faces += 1

            assigns: list[tuple[int, float]] = []
            useful_kf = len(per_kf_assigns)
            if useful_kf > 0:
                votes: dict[int, int] = {}
                max_conf: dict[int, float] = {}
                for d in per_kf_assigns:
                    for cid, conf in d.items():
                        votes[cid] = votes.get(cid, 0) + 1
                        max_conf[cid] = max(max_conf.get(cid, 0.0), conf)
                required = min(cfg.min_keyframe_votes, useful_kf)
                assigns = sorted(
                    [(cid, max_conf[cid]) for cid, v in votes.items() if v >= required],
                    key=lambda x: x[1],
                    reverse=True,
                )
            elif kfs and not cfg.face_exclusive_when_detected:
                # No faces detected in any keyframe → whole-keyframe fallback.
                # Use a bumped margin/threshold internally to reduce background
                # noise contamination.
                q_embs = engine.embed_images([Path(p) for p in kfs])
                assigns = matcher.assign_best_per_query(
                    q_embs, margin=max(cfg.argmax_margin, 0.05)
                )

            if assigns:
                names: list[str] = []
                for char_id, conf in assigns:
                    self.db.assign_character(shot_id, char_id, conf)
                    for e in entries:
                        if e.id == char_id:
                            names.append(e.name)
                            break
                per_shot_names.append(names)
            else:
                per_shot_names.append([])
        print(f"[CorteCenas] Rostos detectados em {shots_with_faces}/{total} shots.")
        if cfg.skip_credit_shots:
            print(f"[CorteCenas] Shots ignorados por créditos/texto: {credit_shots}/{total}")

        return self._finalize_episode(
            cb=cb,
            info=info,
            episode_id=episode_id,
            episode_root=episode_root,
            metadata_dir=metadata_dir,
            bundle=bundle,
            refs_per_char=refs_per_char,
            cut_results=cut_results,
            shot_db_ids=shot_db_ids,
            per_shot_names=per_shot_names,
            name_to_id={e.name: e.id for e in entries},
            characters_json=[
                {
                    "name": e.name,
                    "character_id": e.id,
                    "threshold": e.threshold,
                    "reference_count": len(refs_per_char.get(e.name, [])),
                }
                for e in entries
            ],
        )

    def _finalize_episode(
        self,
        *,
        cb: ProgressCb,
        info: EpisodeInfo,
        episode_id: int,
        episode_root: Path,
        metadata_dir: Path,
        bundle,
        refs_per_char: dict,
        cut_results: list,
        shot_db_ids: list[int],
        per_shot_names: list[list[str]],
        name_to_id: dict[str, int],
        characters_json: list[dict],
    ) -> PipelineResult:
        """Shared end-of-pipeline stage for both CLIP and AI paths:
          - drop characters below min_shots_per_character (cleans DB too)
          - write shots.json + characters.json
          - create by_character / by_pair hardlinks
          - return PipelineResult
        """
        cfg = self.cfg

        from collections import Counter as _Counter
        char_counts = _Counter(n for names in per_shot_names for n in names)
        dropped: list[str] = []
        for name, count in char_counts.items():
            if count >= cfg.min_shots_per_character:
                continue
            cid = name_to_id.get(name)
            if cid is None:
                continue
            with self.db.connect() as c:
                c.execute(
                    "DELETE FROM shot_character WHERE character_id = ?", (cid,)
                )
            dropped.append(f"{name}({count})")
        if dropped:
            drop_set = {d.split("(")[0] for d in dropped}
            per_shot_names = [[n for n in names if n not in drop_set] for names in per_shot_names]
            print(f"[CorteCenas] Removidos (poucos shots): {', '.join(dropped)}")

        cb("organize", -1.0, "Gerando pastas e metadados...")
        clear_grouping(episode_root)

        shots_payload = []
        for (shot, shot_file, kfs), shot_id, names in zip(cut_results, shot_db_ids, per_shot_names):
            assigns = self.db.characters_in_shot(shot_id)
            shot_row = {
                "idx": shot.idx,
                "file": str(shot_file.relative_to(episode_root)).replace("\\", "/"),
                "keyframe": str((kfs[len(kfs) // 2]).relative_to(episode_root)).replace("\\", "/") if kfs else None,
                "start": shot.start,
                "end": shot.end,
            }
            shots_payload.append(
                build_shot_payload(shot_row, bundle.title, info.season, info.episode, assigns)
            )
            if names:
                organize_by_character(shot_file, episode_root, names)
                organize_by_pair(shot_file, episode_root, names)

        write_shots_json(metadata_dir / "shots.json", shots_payload)
        write_characters_json(metadata_dir / "characters.json", characters_json)

        pair_counts = dict(count_pairs(per_shot_names))
        identified = sorted({n for names in per_shot_names for n in names})
        cb("organize", 1.0, "Concluído")
        return PipelineResult(
            episode_root=episode_root,
            total_shots=len(cut_results),
            total_characters=len(identified),
            identified_characters=identified,
            pair_counts=pair_counts,
            anime_title=bundle.title,
            season=info.season,
            episode=info.episode,
            episode_id=episode_id,
        )

    def _run_ai_recognition(
        self,
        *,
        info: EpisodeInfo,
        cb: ProgressCb,
        anime_id: int,
        episode_id: int,
        episode_root: Path,
        metadata_dir: Path,
        bundle,
        refs_per_char: dict,
        cut_results: list,
        ai_mode: AIMode = AIMode.FULL,
    ) -> PipelineResult:
        """AI-only recognition: each shot is classified by sending its
        middle keyframe to the LLM (Gemini via NavyAI). No CLIP, no face
        detection. Slower and costs tokens, but leverages the model's
        prior anime knowledge — useful for well-known series.
        """
        cfg = self.cfg
        cb("embed_refs", -1.0, "Carregando conexão com IA...")
        if not cfg.navyai_api_key.strip():
            raise RuntimeError(
                "Modo IA requer a API key da NavyAI no painel Avançado."
            )

        client = NavyAIClient(
            api_key=cfg.navyai_api_key,
            base_url=cfg.navyai_base_url,
            model=cfg.navyai_model,
        )

        # Face detector for hybrid mode (YOLO -> face crops -> Gemini).
        face_det = None
        if ai_mode == AIMode.HYBRID:
            try:
                face_det = AnimeFaceDetector(ensure_cascade(cfg.models_path))
            except Exception as e:
                print(f"[AI analyze] Não consegui carregar o face detector: {e}")
                face_det = None
                ai_mode = AIMode.FULL

        db_chars = {c["name"]: c for c in self.db.get_characters_for_anime(anime_id)}
        character_names = [ch.name for ch in bundle.characters if refs_per_char.get(ch.name)]
        # Send one reference per character for the top 15 by popularity.
        # We use (role_weight, ref_count) as the popularity proxy: main
        # characters first, then within each role tier the ones with more
        # fan-art/reference coverage win (popular chars have more images).
        _ROLE_RANK = {"Main": 0, "MAIN": 0, "Supporting": 1, "SUPPORTING": 1}
        scored = []
        for ch in bundle.characters:
            paths = refs_per_char.get(ch.name)
            if not paths:
                continue
            role_w = _ROLE_RANK.get(ch.role, 2)
            scored.append((role_w, -len(paths), ch.name))  # negative for desc
        scored.sort()
        top_refs: dict[str, list[bytes]] = {}
        for _, _, name in scored[:15]:
            paths = refs_per_char.get(name, [])
            if not paths:
                continue
            try:
                img = cv2.imread(str(paths[0]))
                if img is None:
                    continue
                h, w = img.shape[:2]
                scale = 256 / max(h, w)
                if scale < 1.0:
                    img = cv2.resize(img, (int(w * scale), int(h * scale)))
                ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    top_refs[name] = [enc.tobytes()]
            except Exception:
                continue
        cb("embed_refs", 1.0, f"IA pronta com {len(character_names)} personagens")

        # 6) Analyze shots with Gemini
        per_shot_names: list[list[str]] = []
        shot_db_ids: list[int] = []
        total = len(cut_results)
        total_pt = 0
        total_ct = 0
        name_to_id = {n: db_chars[n]["id"] for n in character_names if n in db_chars}

        skipped_no_face = 0
        print(f"[AI analyze] Iniciando classificação de {total} shots (modo={ai_mode})...", flush=True)
        for i, (shot, shot_file, kfs) in enumerate(cut_results, 1):
            cb("analyze_shots", i / max(total, 1), f"AI Shot {i}/{total}")
            main_kf = kfs[len(kfs) // 2] if kfs else None
            shot_id = self.db.insert_shot(
                episode_id=episode_id,
                idx=shot.idx,
                file=str(shot_file.relative_to(episode_root)),
                keyframe=str(main_kf.relative_to(episode_root)) if main_kf else None,
                start=shot.start,
                end=shot.end,
            )
            shot_db_ids.append(shot_id)

            if not main_kf or not main_kf.exists():
                per_shot_names.append([])
                continue

            try:
                img = cv2.imread(str(main_kf))
                if img is None:
                    per_shot_names.append([])
                    continue
            except Exception:
                per_shot_names.append([])
                continue

            # --- Hybrid mode: YOLO face crops sent to Gemini ---
            if ai_mode == AIMode.HYBRID and face_det is not None:
                # Use a wider pad than CLIP so hair/headband is visible —
                # critical for similar-faced mains (Chrome vs Senku, etc.).
                faces = face_det.crop_faces(img, pad=cfg.face_crop_padding_ai)
                if not faces:
                    skipped_no_face += 1
                    per_shot_names.append([])
                    continue

                face_bytes_list: list[bytes] = []
                for face in faces[:5]:  # cap 5 faces per shot
                    fh, fw = face.shape[:2]
                    scale = 256 / max(fh, fw)
                    if scale < 1.0:
                        face = cv2.resize(face, (int(fw * scale), int(fh * scale)))
                    ok, enc = cv2.imencode(".jpg", face, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if ok:
                        face_bytes_list.append(enc.tobytes())
                if not face_bytes_list:
                    per_shot_names.append([])
                    continue

                try:
                    verdicts, usage = classify_face_crops(
                        client, face_bytes_list, character_names, bundle.title, top_refs=top_refs
                    )
                except Exception as e:
                    print(f"[AI analyze] Shot #{shot.idx:04d} ERRO: {e}", flush=True)
                    per_shot_names.append([])
                    continue

                pt = int(usage.get("prompt_tokens") or 0)
                ct = int(usage.get("completion_tokens") or 0)
                total_pt += pt
                total_ct += ct

                names_in_shot: list[str] = []
                seen: set[str] = set()
                for (fname, fconf) in verdicts:
                    if fname == "none" or fconf < cfg.default_threshold:
                        continue
                    if fname not in name_to_id:
                        continue
                    if fname in seen:
                        continue
                    seen.add(fname)
                    self.db.assign_character(shot_id, name_to_id[fname], fconf)
                    names_in_shot.append(fname)

                verdict_str = "+".join(names_in_shot) if names_in_shot else "NONE"
                print(
                    f"[AI analyze] Shot #{shot.idx:04d} ({len(face_bytes_list)} faces) -> {verdict_str} "
                    f"| tokens={pt}+{ct}",
                    flush=True,
                )
                per_shot_names.append(names_in_shot)
                continue

            # --- Full-frame mode ---
            try:
                h, w = img.shape[:2]
                scale = 512 / max(h, w)
                if scale < 1.0:
                    img = cv2.resize(img, (int(w * scale), int(h * scale)))
                ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if not ok:
                    per_shot_names.append([])
                    continue
                frame_bytes = enc.tobytes()
            except Exception:
                per_shot_names.append([])
                continue

            try:
                name, conf, reason, usage = classify_frame(
                    client, frame_bytes, character_names, bundle.title, top_refs=top_refs
                )
            except Exception as e:
                print(f"[AI analyze] Shot #{shot.idx:04d} ERRO: {e}", flush=True)
                per_shot_names.append([])
                continue

            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            total_pt += pt
            total_ct += ct

            verdict = name or "NONE"
            print(
                f"[AI analyze] Shot #{shot.idx:04d} -> {verdict} (conf={conf:.2f}) "
                f"| tokens={pt}+{ct} | {(reason or '')[:70]}",
                flush=True,
            )

            if name and conf >= cfg.default_threshold and name in name_to_id:
                self.db.assign_character(shot_id, name_to_id[name], conf)
                per_shot_names.append([name])
            else:
                per_shot_names.append([])

        client.close()

        cost_est = total_pt / 1_000_000 * 0.075 + total_ct / 1_000_000 * 0.30
        extra = f" | shots sem rosto (pulados): {skipped_no_face}" if ai_mode == AIMode.HYBRID else ""
        print(
            f"[AI analyze] === FIM === tokens: {total_pt:,}+{total_ct:,} "
            f"(total {total_pt + total_ct:,}) | custo estimado: ${cost_est:.4f}{extra}",
            flush=True,
        )

        return self._finalize_episode(
            cb=cb,
            info=info,
            episode_id=episode_id,
            episode_root=episode_root,
            metadata_dir=metadata_dir,
            bundle=bundle,
            refs_per_char=refs_per_char,
            cut_results=cut_results,
            shot_db_ids=shot_db_ids,
            per_shot_names=per_shot_names,
            name_to_id=name_to_id,
            characters_json=[
                {
                    "name": n,
                    "character_id": name_to_id.get(n),
                    "threshold": cfg.default_threshold,
                    "reference_count": len(refs_per_char.get(n, [])),
                }
                for n in character_names
            ],
        )
