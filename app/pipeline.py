from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .ai_review import NavyAIClient, QuotaExhaustedError, classify_face_crops, classify_frame
from .config import Config
from .keyframe_extractor import cut_all_shots
from .matching.character_matcher import CharacterEntry, CharacterMatcher, build_centroid
from .matching.cooccurrence import count_pairs
from .matching.credit_detector import is_credits_frame
from .matching.embedding_engine import EmbeddingEngine, from_bytes, to_bytes
from .matching.face_detector import AnimeFaceDetector, ensure_cascade, smart_portrait_crop
# Lightweight types re-exported here so callers can keep `from .pipeline
# import AIMode, PipelineResult, STAGES` without dragging in torch just to
# read a type name. UI modules should prefer `from .pipeline_types import ...`.
from .matching.face_clustering import FaceObservation, cluster_faces, pick_representatives
from .pipeline_types import (
    AIMode,
    DiscoveredGroup,
    DiscoveryResult,
    DiscoveryShot,
    InsufficientRefsError,
    PipelineResult,
    ProgressCb,
    STAGES,
)
from .providers.anime_provider import (
    AnimeBundle,
    AnimeProvider,
    CharacterRef,
    local_cache_id,
)
from .references.reference_store import ReferenceStore
from .shot_detection import ShotBounds, detect_shots
from .storage.db import Database
from .storage.metadata_writer import build_shot_payload, write_characters_json, write_shots_json
from .storage.organizer import clear_grouping, organize_by_character, organize_by_pair, sanitize
from .video_ingest import EpisodeInfo


def _clip_needs_download(model_name: str, pretrained: str) -> bool:
    """Return True if the open_clip weights aren't in the local cache yet.
    Shows a helpful 'first-run download' message when we know we'd hit the
    network. False on lookup failure — better to say nothing than to warn
    incorrectly. open_clip today caches through huggingface_hub for the
    OpenAI-hosted checkpoints (repo `timm/vit_*_clip_224.openai`), so we
    ask huggingface_hub whether the file is already there.
    """
    try:
        import open_clip
        from huggingface_hub import try_to_load_from_cache

        cfg = open_clip.get_pretrained_cfg(model_name, pretrained) or {}
        hf_hub = cfg.get("hf_hub") if isinstance(cfg, dict) else None
        if not hf_hub:
            return False
        repo_id = hf_hub.rstrip("/")
        for filename in ("open_clip_model.safetensors", "open_clip_pytorch_model.bin"):
            hit = try_to_load_from_cache(repo_id=repo_id, filename=filename)
            if hit and hit != "_CACHED_NO_EXIST":
                return False
        return True
    except Exception:
        return False


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
        ai_review_ambiguous: bool = False,
    ) -> PipelineResult:
        cb = on_progress or _noop
        cfg = self.cfg

        episode_root, metadata_dir, cut_results = self._prepare_shots(info, cb)

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
        if bundle.cache_id_override:
            cache_id = bundle.cache_id_override   # banco local (Modo Descoberta)
        elif bundle.franchise_root_id:
            cache_id = f"al{bundle.franchise_root_id}"
        elif bundle.anilist_id:
            cache_id = f"al{bundle.anilist_id}"
        else:
            cache_id = f"mal{bundle.mal_id}"

        # Pastas locais de personagem que NÃO batem com o elenco online
        # também são personagens (Modo Descoberta com nome digitado, pastas
        # criadas à mão). Sem isto, as fotos delas ficavam invisíveis — bug
        # real: usuário batizou "Mitsuhime", o MAL chama de "Yukishiro,
        # Mitsuhime", e a análise dizia "sem referências suficientes".
        extra_locals = self._local_only_characters(ref_store, cache_id, bundle)
        for name in extra_locals:
            bundle.characters.append(
                CharacterRef(mal_id=None, anilist_id=None, name=name,
                             role="Main", image_urls=[])
            )
            self.db.upsert_character(
                anime_id=anime_id, name=name,
                anilist_id=None, mal_id=None, role="Main",
            )
        if extra_locals:
            cb("download_refs", -1.0,
               f"+{len(extra_locals)} personagens de pastas locais")

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
        clip_msg = "Carregando modelo CLIP..."
        if _clip_needs_download(cfg.clip_model, cfg.clip_pretrained):
            clip_msg = (
                "Baixando modelo CLIP (~890 MB) — só na primeira execução, "
                "depois fica cacheado. Pode demorar 1-3 min."
            )
        cb("embed_refs", -1.0, clip_msg)
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

        # Zero characters with embeddings = nothing can ever match. Seen in
        # the wild when Jikan's pictures endpoint 504s for every character:
        # each one falls back to a single portrait and the min_references
        # filter drops them all. Analyzing 300+ shots against an empty bank
        # would burn minutes of GPU to deliver a guaranteed-empty result —
        # fail now, with the actual reason and a way out.
        if not entries:
            raise InsufficientRefsError(
                "Nenhum personagem ficou com fotos de referência suficientes "
                f"(mínimo {cfg.min_references_per_character} por personagem) — "
                "a análise não teria como identificar ninguém.\n\n"
                "Causa mais comum: as fontes de imagens (Jikan/MyAnimeList) "
                "estão instáveis ou fora do ar agora. O que fazer:\n"
                "• Tente de novo mais tarde (os shots cortados ficam em cache, "
                "a próxima rodada pula direto pro banco de personagens);\n"
                "• Ou adicione fotos manualmente: 'Abrir pasta de refs' neste "
                "aviso — cada personagem tem uma subpasta; prints do próprio "
                "episódio funcionam.\n\n"
                "Detalhes por personagem no app.log (Configurações → Abrir "
                "pasta de logs).",
                refs_dir=str(ref_store.anime_dir(cache_id) / "characters"),
            )

        # 1-2 usable characters while others got dropped: the run still
        # works, but the user should know most of the cast is invisible.
        refs_dir_str = str(ref_store.anime_dir(cache_id) / "characters")
        low_refs_warning = None
        if skipped_few_refs and len(entries) <= 2:
            low_refs_warning = (
                f"Só {len(entries)} personagem(ns) tinham fotos de referência "
                f"suficientes — {len(skipped_few_refs)} ficaram de fora e não "
                "podem ser identificados neste episódio."
            )

        matcher = CharacterMatcher(entries)

        # 6) Analyze shots — face_det reused from step 5

        per_shot_names: list[list[str]] = []
        shot_db_ids: list[int] = []
        total = len(cut_results)
        shots_with_faces = 0
        credit_shots = 0
        # Modo híbrido: shots que o CLIP deixou SEM personagem mas cuja melhor
        # similaridade chegou perto do threshold — a zona cinzenta que vale
        # uma segunda opinião da IA. Guardamos os crops já extraídos.
        ambiguous: list[dict] = []
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
            shot_best_sim = 0.0
            shot_crops: list = []
            for kf_path in kfs:
                img = cv2.imread(str(kf_path))
                if img is None:
                    continue
                crops = face_det.crop_faces(img, pad=cfg.face_crop_padding)
                if not crops:
                    continue
                had_any_faces = True
                if ai_review_ambiguous:
                    shot_crops.extend(crops)
                embs = engine.embed_images(crops)
                d = dict(matcher.assign_best_per_query(embs, margin=cfg.argmax_margin))
                per_kf_assigns.append(d)
                if ai_review_ambiguous:
                    best = matcher.best_overall(embs)
                    if best is not None:
                        shot_best_sim = max(shot_best_sim, best[1])

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
                # Ficou sem dono mas chegou perto? Candidato à revisão da IA.
                if (
                    ai_review_ambiguous
                    and shot_crops
                    and cfg.ai_review_low <= shot_best_sim < cfg.default_threshold
                ):
                    crops_jpg: list[bytes] = []
                    for c in shot_crops[:5]:
                        ch_, cw_ = c.shape[:2]
                        scale = 256 / max(ch_, cw_)
                        if scale < 1.0:
                            c = cv2.resize(c, (int(cw_ * scale), int(ch_ * scale)))
                        ok_, enc_ = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if ok_:
                            crops_jpg.append(enc_.tobytes())
                    if crops_jpg:
                        ambiguous.append({
                            "pos": len(per_shot_names) - 1,  # índice em per_shot_names
                            "shot": shot,
                            "shot_id": shot_id,
                            "crops": crops_jpg,
                            "best_sim": shot_best_sim,
                        })
        print(f"[CorteCenas] Rostos detectados em {shots_with_faces}/{total} shots.")
        if cfg.skip_credit_shots:
            print(f"[CorteCenas] Shots ignorados por créditos/texto: {credit_shots}/{total}")

        # === Revisão IA dos duvidosos (modo híbrido) ===
        # O CLIP resolveu o grosso de graça; só a zona cinzenta gasta API.
        if ai_review_ambiguous and ambiguous:
            name_to_id_review = {e.name: e.id for e in entries}
            roster = [e.name for e in entries]
            # Mais promissores primeiro; teto de custo por episódio.
            ambiguous.sort(key=lambda a: -a["best_sim"])
            dropped = len(ambiguous) - cfg.ai_review_max_shots
            if dropped > 0:
                print(f"[AI review] {dropped} duvidosos além do teto de "
                      f"{cfg.ai_review_max_shots} ficaram de fora.")
                ambiguous = ambiguous[: cfg.ai_review_max_shots]
            cb("ai_review", -1.0, f"{len(ambiguous)} shots duvidosos → IA")
            try:
                client = self._build_ai_client()
            except RuntimeError as e:
                print(f"[AI review] Pulado: {e}", flush=True)
                client = None
            if client is not None:
                from .ai_review import QuotaExhaustedError as _Quota
                top_refs = self._build_top_refs(bundle, refs_per_char)
                confirmed = 0
                errors = 0
                total_pt = total_ct = 0
                try:
                    for j, item in enumerate(ambiguous, 1):
                        cb("ai_review", j / len(ambiguous),
                           f"IA revisando {j}/{len(ambiguous)}")
                        try:
                            verdicts, usage = classify_face_crops(
                                client, item["crops"], roster,
                                bundle.title, top_refs=top_refs,
                            )
                        except _Quota as e:
                            print(f"[AI review] Quota esgotou no {j}º duvidoso — "
                                  f"mantendo o resultado do CLIP. {e}", flush=True)
                            break
                        except Exception as e:
                            errors += 1
                            print(f"[AI review] Shot #{item['shot'].idx:04d} ERRO: {e}",
                                  flush=True)
                            if errors >= 5:
                                print("[AI review] Muitos erros seguidos — parando a "
                                      "revisão; resultado do CLIP mantido.", flush=True)
                                break
                            continue
                        errors = 0
                        total_pt += int(usage.get("prompt_tokens") or 0)
                        total_ct += int(usage.get("completion_tokens") or 0)
                        names_in_shot: list[str] = []
                        for (vname, vconf) in verdicts:
                            if vname == "none" or vconf < cfg.default_threshold:
                                continue
                            if vname not in name_to_id_review or vname in names_in_shot:
                                continue
                            self.db.assign_character(
                                item["shot_id"], name_to_id_review[vname], vconf
                            )
                            names_in_shot.append(vname)
                        if names_in_shot:
                            confirmed += 1
                            per_shot_names[item["pos"]] = names_in_shot
                            print(f"[AI review] Shot #{item['shot'].idx:04d} "
                                  f"(sim {item['best_sim']:.2f}) -> "
                                  f"{'+'.join(names_in_shot)}", flush=True)
                finally:
                    client.close()
                print(f"[AI review] === FIM === {confirmed}/{len(ambiguous)} duvidosos "
                      f"confirmados | tokens: {total_pt:,}+{total_ct:,}", flush=True)
                cb("ai_review", 1.0,
                   f"IA confirmou {confirmed} de {len(ambiguous)} duvidosos")
        elif ai_review_ambiguous:
            cb("ai_review", 1.0, "Nenhum shot duvidoso — CLIP resolveu tudo")
        else:
            cb("ai_review", 1.0, "—")

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
            refs_dir=refs_dir_str,
            low_refs_warning=low_refs_warning,
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
        refs_dir: str | None = None,
        low_refs_warning: str | None = None,
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
            low_refs_warning=low_refs_warning,
            refs_dir=refs_dir,
        )

    # ================= Modo Descoberta =================

    def run_discovery(
        self, info: EpisodeInfo, on_progress: ProgressCb | None = None
    ) -> DiscoveryResult:
        """Análise SEM banco online: corta o episódio, detecta e embedda os
        rostos, e agrupa por identidade. Retorna os grupos anônimos pra tela
        de batismo — commit_discovery() fecha o trabalho com os nomes."""
        cb = on_progress or _noop
        cfg = self.cfg
        episode_root, metadata_dir, cut_results = self._prepare_shots(info, cb)

        # Identidade online é opcional na descoberta: se o anime resolver,
        # os grupos reforçam o banco REAL (refs em al<root>) e os nomes são
        # pré-sugeridos pelos centroides já conhecidos; se não resolver,
        # segue 100% offline com banco local.
        cb("fetch_characters", -1.0, "Buscando anime (descoberta)...")
        online_bundle = None
        provider = AnimeProvider(cfg.cache_path)
        try:
            online_bundle = provider.resolve(
                info.anime,
                max_characters=cfg.max_characters_per_anime,
                images_per_character=cfg.references_per_character,
                on_status=lambda m: cb("fetch_characters", -1.0, m),
                use_danbooru=False,
                season=info.season,
            )
        except Exception as e:
            print(f"[Descoberta] Sem identidade online ({type(e).__name__}) — "
                  "seguindo com banco local.", flush=True)
        finally:
            provider.close()

        known_centroids: list[tuple[str, np.ndarray]] = []
        if online_bundle is not None:
            anime_title = online_bundle.title
            anime_id = self.db.upsert_anime(
                anilist_id=online_bundle.anilist_id,
                mal_id=online_bundle.mal_id,
                title=online_bundle.title,
                title_english=online_bundle.title_english,
            )
            if online_bundle.cache_id_override:
                disc_cache_id = online_bundle.cache_id_override
            elif online_bundle.franchise_root_id:
                disc_cache_id = f"al{online_bundle.franchise_root_id}"
            elif online_bundle.anilist_id:
                disc_cache_id = f"al{online_bundle.anilist_id}"
            else:
                disc_cache_id = f"mal{online_bundle.mal_id}"
            for row in self.db.get_characters_for_anime(anime_id):
                if row.get("embedding"):
                    known_centroids.append((row["name"], from_bytes(row["embedding"])))
            cb("fetch_characters", 1.0,
               f"{anime_title} — descoberta reforçando o banco existente")
        else:
            anime_title = info.anime
            anime_id = self.db.upsert_anime(
                anilist_id=None, mal_id=None, title=info.anime, title_english=None
            )
            disc_cache_id = local_cache_id(info.anime)
            cb("fetch_characters", 1.0, "Modo Descoberta — sem banco online")

        # Refs pra SUGESTÃO: quando o anime é conhecido mas nunca foi
        # analisado (sem centroides no DB), até 1 foto por personagem —
        # pouco demais pra análise — já serve pra pré-nomear grupos.
        weak_refs: dict[str, list[Path]] = {}
        if online_bundle is not None and not known_centroids:
            cb("download_refs", -1.0, "Baixando fotos pra sugerir nomes...")
            try:
                weak_refs = ReferenceStore(cfg.cache_path).ensure_references(
                    disc_cache_id, online_bundle,
                    on_status=lambda m: cb("download_refs", -1.0, m),
                )
            except Exception as e:
                print(f"[Descoberta] Refs pra sugestão falharam: {e}", flush=True)
        cb("download_refs", 1.0, "—")

        clip_msg = "Carregando modelo CLIP..."
        if _clip_needs_download(cfg.clip_model, cfg.clip_pretrained):
            clip_msg = (
                "Baixando modelo CLIP (~890 MB) — só na primeira execução, "
                "depois fica cacheado. Pode demorar 1-3 min."
            )
        cb("embed_refs", -1.0, clip_msg)
        engine = EmbeddingEngine(
            model_name=cfg.clip_model,
            pretrained=cfg.clip_pretrained,
            use_cuda=cfg.use_cuda,
        )
        face_det = AnimeFaceDetector(ensure_cascade(cfg.models_path))

        # Centroides provisórios das refs escassas (mesmo recorte de rosto da
        # análise normal). Só entram nomes que ainda não têm centroide do DB.
        if weak_refs:
            have = {n for n, _ in known_centroids}
            for name, paths in weak_refs.items():
                if name in have or not paths:
                    continue
                imgs = []
                for p in paths[:4]:
                    img = cv2.imread(str(p))
                    if img is None:
                        continue
                    crops = face_det.crop_faces(img, pad=cfg.face_crop_padding)
                    imgs.extend(crops if crops else [smart_portrait_crop(img)])
                if not imgs:
                    continue
                centroid = build_centroid(engine.embed_images(imgs))
                if centroid is not None:
                    known_centroids.append((name, centroid))
            print(f"[Descoberta] {len(known_centroids)} centroides provisórios "
                  "pra sugestão de nomes.", flush=True)
        cb("embed_refs", 1.0, "Modelos prontos")

        episode_id = self.db.upsert_episode(
            anime_id, info.season, info.episode, str(info.source)
        )
        self.db.clear_episode_shots(episode_id)

        observations: list[FaceObservation] = []
        shots_out: list[DiscoveryShot] = []
        total = len(cut_results)
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
            shots_out.append(DiscoveryShot(
                pos=i - 1, shot_id=shot_id, idx=shot.idx,
                file=str(shot_file), keyframes=[str(k) for k in kfs],
                start=shot.start, end=shot.end,
            ))
            for kf_path in kfs:
                img = cv2.imread(str(kf_path))
                if img is None:
                    continue
                crops = face_det.crop_faces(img, pad=cfg.face_crop_padding)
                if not crops:
                    continue
                embs = engine.embed_images(crops)
                for crop, emb in zip(crops, embs):
                    ch_, cw_ = crop.shape[:2]
                    scale = 256 / max(ch_, cw_)
                    if scale < 1.0:
                        crop = cv2.resize(crop, (int(cw_ * scale), int(ch_ * scale)))
                    ok_, enc_ = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
                    if ok_:
                        observations.append(FaceObservation(
                            shot_pos=i - 1, shot_id=shot_id, shot_idx=shot.idx,
                            embedding=np.asarray(emb, dtype=np.float32),
                            crop_jpg=enc_.tobytes(),
                        ))

        cb("ai_review", 1.0, "—")
        cb("organize", -1.0, f"Agrupando {len(observations)} rostos por personagem...")
        clusters = cluster_faces(observations)
        # Sugestão de nome: centroide do grupo vs personagens já conhecidos
        # (existem quando o anime já foi analisado antes). 0.75 é conservador
        # — melhor campo vazio que sugestão errada pré-preenchida.
        _SUGGEST_MIN = 0.75
        groups: list[DiscoveredGroup] = []
        for key, cl in enumerate(clusters):
            reps = pick_representatives(cl, observations, k=10)
            positions = sorted({observations[i].shot_pos for i in cl.members})
            conf: dict[int, float] = {}
            for i in cl.members:
                p = observations[i].shot_pos
                s = float(observations[i].embedding @ cl.centroid)
                conf[p] = max(conf.get(p, 0.0), s)
            suggested, s_sim = "", 0.0
            for kname, kcent in known_centroids:
                s = float(cl.centroid @ kcent)
                if s > s_sim:
                    s_sim, suggested = s, kname
            if s_sim < _SUGGEST_MIN:
                suggested, s_sim = "", 0.0
            groups.append(DiscoveredGroup(
                key=key,
                n_faces=len(cl.members),
                n_shots=len(positions),
                thumbs_jpg=[observations[i].crop_jpg for i in reps[:6]],
                ref_crops_jpg=[observations[i].crop_jpg for i in reps[:8]],
                shot_ids=sorted({observations[i].shot_id for i in cl.members}),
                shot_positions=positions,
                shot_conf=conf,
                centroid_bytes=to_bytes(cl.centroid),
                suggested_name=suggested,
                suggested_sim=s_sim,
            ))
        cb("organize", 1.0, f"{len(groups)} personagens descobertos")
        print(f"[Descoberta] {len(observations)} rostos → {len(groups)} grupos "
              f"({', '.join(str(g.n_faces) for g in groups[:10])}...)", flush=True)

        return DiscoveryResult(
            anime_title=anime_title,
            season=info.season,
            episode=info.episode,
            anime_id=anime_id,
            episode_id=episode_id,
            episode_root=episode_root,
            cache_id=disc_cache_id,
            shots=shots_out,
            groups=groups,
            total_faces=len(observations),
            online=online_bundle is not None,
            roster=(
                [ch.name for ch in online_bundle.characters]
                if online_bundle is not None else []
            ),
        )

    def commit_discovery(
        self,
        result: DiscoveryResult,
        names: dict[int, str],
        on_progress: ProgressCb | None = None,
    ) -> PipelineResult:
        """Fecha o Modo Descoberta com os nomes dados pelo usuário: cria os
        personagens, atribui os shots, salva os crops como referências (os
        próximos episódios rodam no modo normal) e organiza as pastas.
        Grupos sem nome são ignorados; dois grupos com o MESMO nome fundem."""
        cb = on_progress or _noop
        cfg = self.cfg

        by_name: dict[str, list[DiscoveredGroup]] = {}
        for g in result.groups:
            name = (names.get(g.key) or "").strip()
            if name:
                by_name.setdefault(name, []).append(g)
        if not by_name:
            raise RuntimeError(
                "Nenhum grupo recebeu nome — não há o que salvar. "
                "Dê nome a pelo menos um personagem e confirme de novo."
            )

        cb("organize", -1.0, "Salvando personagens descobertos...")
        store = ReferenceStore(cfg.cache_path)
        name_to_id: dict[str, int] = {}
        refs_per_char: dict[str, list[Path]] = {}

        for name, gs in by_name.items():
            cid = self.db.upsert_character(
                anime_id=result.anime_id, name=name,
                anilist_id=None, mal_id=None, role="Main",
            )
            name_to_id[name] = cid

            d = store.character_dir(result.cache_id, name)
            d.mkdir(parents=True, exist_ok=True)
            paths: list[Path] = []
            for g in gs:
                for jpg in g.ref_crops_jpg:
                    if len(paths) >= 10:
                        break
                    p = d / f"auto_disc_{len(paths):02d}.jpg"
                    p.write_bytes(jpg)
                    paths.append(p)
            refs_per_char[name] = paths

            cents = np.stack([from_bytes(g.centroid_bytes) for g in gs])
            weights = np.array([g.n_faces for g in gs], dtype=np.float32)
            centroid = (cents * weights[:, None]).sum(axis=0)
            centroid = centroid / max(float(np.linalg.norm(centroid)), 1e-8)
            self.db.set_character_embedding(
                cid, to_bytes(centroid.astype(np.float32)), reference_count=len(paths)
            )

            for g in gs:
                for pos in g.shot_positions:
                    self.db.assign_character(
                        result.shots[pos].shot_id, cid,
                        float(g.shot_conf.get(pos, 0.9)),
                    )

        per_shot_names: list[list[str]] = [[] for _ in result.shots]
        for name, gs in by_name.items():
            for g in gs:
                for pos in g.shot_positions:
                    if name not in per_shot_names[pos]:
                        per_shot_names[pos].append(name)

        bundle = AnimeBundle(
            anilist_id=None, mal_id=None,
            title=result.anime_title, title_english=None,
            characters=[
                CharacterRef(mal_id=None, anilist_id=None, name=n,
                             role="Main", image_urls=[])
                for n in by_name
            ],
            cache_id_override=result.cache_id,
        )
        if not result.online:
            # Banco local persistido: a próxima análise deste anime resolve
            # offline (anime_provider cai nele quando as buscas falham).
            # Quando o anime é conhecido, os refs já entraram na pasta real
            # (al<root>) e o metadata online se resolve sozinho.
            provider = AnimeProvider(cfg.cache_path)
            try:
                provider.save_cache(result.cache_id, bundle)
            finally:
                provider.close()

        cut_results = [
            (ShotBounds(idx=s.idx, start=s.start, end=s.end),
             Path(s.file), [Path(k) for k in s.keyframes])
            for s in result.shots
        ]
        info = EpisodeInfo(
            anime=result.anime_title, season=result.season,
            episode=result.episode,
            source=Path(result.shots[0].file) if result.shots else Path("."),
        )
        return self._finalize_episode(
            cb=cb,
            info=info,
            episode_id=result.episode_id,
            episode_root=result.episode_root,
            metadata_dir=result.episode_root / "metadata",
            bundle=bundle,
            refs_per_char=refs_per_char,
            cut_results=cut_results,
            shot_db_ids=[s.shot_id for s in result.shots],
            per_shot_names=per_shot_names,
            name_to_id=name_to_id,
            characters_json=[
                {
                    "name": n,
                    "character_id": name_to_id[n],
                    "threshold": cfg.default_threshold,
                    "reference_count": len(refs_per_char.get(n, [])),
                }
                for n in by_name
            ],
            refs_dir=str(store.anime_dir(result.cache_id) / "characters"),
        )

    def _prepare_shots(self, info: EpisodeInfo, cb: ProgressCb):
        """Estágios comuns a TODAS as análises (normal e descoberta):
        layout de pastas, detecção de shots (com cache) e corte+keyframes.
        Retorna (episode_root, metadata_dir, cut_results)."""
        cfg = self.cfg
        cb("parse", 1.0, f"{info.anime} {info.slug}")

        episode_root = cfg.output_path / sanitize(info.anime) / info.slug
        shots_dir = episode_root / "shots"
        keyframes_dir = episode_root / "keyframes"
        metadata_dir = episode_root / "metadata"
        for d in (shots_dir, keyframes_dir, metadata_dir):
            d.mkdir(parents=True, exist_ok=True)

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
                on_progress=lambda f: cb(
                    "detect_shots", f, f"Analisando mudanças de cena... {int(f * 100)}%"
                ),
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
        return episode_root, metadata_dir, cut_results

    @staticmethod
    def _local_only_characters(ref_store: ReferenceStore, cache_id: str, bundle) -> list[str]:
        """Nomes de pastas em <anime>/characters/ com imagens dentro que não
        correspondem (por slug) a nenhum personagem do bundle online."""
        from .references.reference_store import slug_for
        chars_dir = ref_store.anime_dir(cache_id) / "characters"
        if not chars_dir.exists():
            return []
        known = {slug_for(ch.name) for ch in bundle.characters}
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        out: list[str] = []
        for d in sorted(chars_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            if d.name in known:
                continue
            if any(f.suffix.lower() in exts for f in d.iterdir() if f.is_file()):
                out.append(d.name)
        return out

    def _build_ai_client(self) -> NavyAIClient:
        """NavyAI primário + Gemini nativo como fallback, conforme as keys
        configuradas. Levanta RuntimeError se nenhuma key existir."""
        cfg = self.cfg
        primary_key = cfg.navyai_api_key.strip()
        gemini_key = cfg.gemini_api_key.strip()
        if not primary_key and not gemini_key:
            raise RuntimeError(
                "Modo IA requer uma API key (NavyAI ou Gemini) em Configurações."
            )
        from .ai_review import GEMINI_OPENAI_BASE
        fallback = None
        if gemini_key:
            fallback = NavyAIClient(
                api_key=gemini_key,
                base_url=GEMINI_OPENAI_BASE,
                model=cfg.gemini_model or "gemini-2.5-flash",
            )
        if primary_key:
            return NavyAIClient(
                api_key=primary_key,
                base_url=cfg.navyai_base_url,
                model=cfg.navyai_model,
                fallback=fallback,
            )
        return fallback  # Gemini-only path

    @staticmethod
    def _build_top_refs(bundle, refs_per_char: dict) -> dict[str, list[bytes]]:
        """One reference image per character for the top 15 by popularity
        (role weight, then ref count) — the visual roster sent to the AI."""
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
        return top_refs

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
        client = self._build_ai_client()

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
        if bundle.cache_id_override:
            _ai_cache_id = bundle.cache_id_override
        elif bundle.franchise_root_id:
            _ai_cache_id = f"al{bundle.franchise_root_id}"
        elif bundle.anilist_id:
            _ai_cache_id = f"al{bundle.anilist_id}"
        else:
            _ai_cache_id = f"mal{bundle.mal_id}"
        refs_dir_str = str(
            ReferenceStore(cfg.cache_path).anime_dir(_ai_cache_id) / "characters"
        )
        if not character_names:
            raise InsufficientRefsError(
                "Nenhum personagem tem foto de referência — a IA não teria "
                "nomes nem rostos pra comparar.\n\n"
                "Causa mais comum: as fontes de imagens (Jikan/MyAnimeList) "
                "estão instáveis ou fora do ar agora. Tente de novo mais "
                "tarde (os shots cortados ficam em cache) ou adicione fotos "
                "manualmente pela pasta de refs.\n\n"
                "Detalhes no app.log (Configurações → Abrir pasta de logs).",
                refs_dir=refs_dir_str,
            )
        low_refs_warning = None
        n_missing = len(bundle.characters) - len(character_names)
        if len(character_names) <= 2 and n_missing > 0:
            low_refs_warning = (
                f"Só {len(character_names)} personagem(ns) tinham fotos de "
                f"referência — {n_missing} ficaram de fora e não podem ser "
                "identificados neste episódio."
            )
        top_refs = self._build_top_refs(bundle, refs_per_char)
        cb("embed_refs", 1.0, f"IA pronta com {len(character_names)} personagens")

        # 6) Analyze shots with Gemini
        per_shot_names: list[list[str]] = []
        shot_db_ids: list[int] = []
        total = len(cut_results)
        total_pt = 0
        total_ct = 0
        name_to_id = {n: db_chars[n]["id"] for n in character_names if n in db_chars}

        skipped_no_face = 0
        # A dead model / bad key fails EVERY request the same way. Without a
        # circuit breaker the run grinds through all N shots (minutes of
        # retries + wasted quota) to deliver an empty result.
        consecutive_ai_errors = 0
        max_consecutive_ai_errors = 8

        def _register_ai_error(shot_idx: int, err: Exception) -> None:
            nonlocal consecutive_ai_errors
            if isinstance(err, QuotaExhaustedError):
                # Every configured provider is out of quota for the day —
                # each further shot would fail identically.
                client.close()
                raise RuntimeError(
                    "Quota diária de IA esgotada em todos os provedores "
                    "configurados — análise abortada.\n\n"
                    f"Detalhe: {err}\n\n"
                    "Opções: esperar o reset da quota (NavyAI e Gemini resetam "
                    "1x por dia), configurar outra API key em Configurações, ou "
                    "usar o botão 'Analisar episódio' (CLIP local, sem IA e sem "
                    "limite de uso)."
                )
            consecutive_ai_errors += 1
            print(f"[AI analyze] Shot #{shot_idx:04d} ERRO: {err}", flush=True)
            if consecutive_ai_errors >= max_consecutive_ai_errors:
                client.close()
                raise RuntimeError(
                    f"A IA falhou em {consecutive_ai_errors} shots seguidos — análise "
                    f"abortada pra não desperdiçar tempo e quota.\n\n"
                    f"Último erro: {err}\n\n"
                    "Confira o modelo e as API keys em Configurações. O detalhe "
                    "completo de cada tentativa está no app.log (Configurações → "
                    "Abrir pasta de logs)."
                )

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
                    _register_ai_error(shot.idx, e)
                    per_shot_names.append([])
                    continue

                pt = int(usage.get("prompt_tokens") or 0)
                ct = int(usage.get("completion_tokens") or 0)
                total_pt += pt
                total_ct += ct

                # Faces were sent but nothing usable came back (empty content /
                # unparseable JSON — a real "none" verdict still yields one
                # entry per face). That's a malfunction, not a miss: count it
                # toward the circuit breaker instead of silently burning the
                # whole episode's quota on 200-but-empty responses.
                if not verdicts:
                    _register_ai_error(
                        shot.idx,
                        RuntimeError(
                            f"resposta da IA sem conteúdo utilizável "
                            f"(tokens={pt}+{ct}) — veja 'resposta 200 mas "
                            f"VAZIA' no app.log"
                        ),
                    )
                    per_shot_names.append([])
                    continue
                consecutive_ai_errors = 0

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
                _register_ai_error(shot.idx, e)
                per_shot_names.append([])
                continue
            consecutive_ai_errors = 0

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
        cb("ai_review", 1.0, "—")  # estágio não se aplica no modo IA puro

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
            refs_dir=refs_dir_str,
            low_refs_warning=low_refs_warning,
        )
