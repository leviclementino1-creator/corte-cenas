from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import cv2
import ffmpeg

from .ffmpeg_locate import ffmpeg_binary, nvenc_available, run_ffmpeg_hidden
from .imagens import gravar_imagem
from .shot_detection import ShotBounds

_avisou_escrita = False


def _falhou_a_escrita(out: Path) -> None:
    """Um aviso, uma vez por execução.

    O silêncio aqui é que custou caro: o `cv2.imwrite` devolvia False, o
    código appendava o caminho do mesmo jeito, e o app terminava a análise
    dizendo "pronto" com 1470 keyframes que não existiam. Uma linha no log
    teria resolvido em minutos.
    """
    global _avisou_escrita
    if _avisou_escrita:
        return
    _avisou_escrita = True
    print(f"[CorteCenas] NAO consegui gravar o keyframe em {out} — sem eles a "
          "analise fica sem imagem pra identificar personagem. Confira espaco "
          "em disco e permissao da pasta.", flush=True)


def _no_window_flag() -> int:
    """CREATE_NO_WINDOW no Windows — senão pisca um console preto por
    invocação no app congelado."""
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Consumer GeForce cards cap concurrent NVENC sessions (3-8 depending on the
# driver generation). 3 parallel encodes is safe everywhere and already keeps
# the encode chip saturated for 1-4s clips.
_NVENC_WORKERS = 3
# libx264 path: each ffmpeg spawns its own encoder threads, so a modest pool
# is enough to keep every core busy without thrashing.
_CPU_WORKERS = 4


def cut_shot(
    video_path: str | Path,
    shot: ShotBounds,
    out_file: Path,
    reencode: bool = True,
    use_nvenc: bool = False,
    fps: float = 24.0,
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

    # O "fim" do shot é o timestamp do PRIMEIRO frame do shot seguinte (fim
    # exclusivo, convenção do PySceneDetect). Cortar com "-to fim" na ENTRADA
    # deixava exatamente esse frame invadir o clipe quando o arredondamento de
    # timestamp caía pro lado errado (MKV marca em milissegundos) — o clássico
    # "frame de outra cena no final". A correção: duração na SAÍDA (-t, exata
    # pós-decodificação) com margem de MEIO frame — o intruso nunca entra e o
    # último frame legítimo nunca sai (ele termina 1 frame inteiro antes).
    duration = max(0.05, shot.duration - 0.5 / max(fps, 1.0))

    if reencode:
        if use_nvenc:
            # GPU encode chip: ~5-10x faster than libx264 on CPU and leaves
            # the CPU free for parallel decode/keyframes. rc=vbr + cq + b:v 0
            # is NVENC's constant-quality mode (the crf equivalent).
            stream = ffmpeg.input(str(video_path), ss=shot.start).output(
                str(out_file),
                t=duration,
                vcodec="h264_nvenc",
                preset="p4",
                rc="vbr",
                cq=23,
                acodec="aac",
                format="mp4",
                movflags="+faststart",
                loglevel="error",
                **{"b:v": "0"},
            )
        else:
            stream = ffmpeg.input(str(video_path), ss=shot.start).output(
                str(out_file),
                t=duration,
                vcodec="libx264",
                preset="ultrafast",
                crf=20,
                acodec="aac",
                format="mp4",
                movflags="+faststart",
                loglevel="error",
            )
    else:
        # ARMADILHA CONHECIDA: `-c copy` só consegue cortar em keyframe, e o
        # GOP dos arquivos típicos aqui é 2.002s — ou seja, o clipe começa
        # até DOIS SEGUNDOS antes, com o final da cena anterior colado nele.
        # Parece "modo rápido" e entrega clipe errado. Fica só como escape
        # manual (cfg.reencode_shots), com aviso no log pra ninguém culpar
        # o reconhecimento por um erro de corte.
        print(
            f"[CorteCenas] AVISO shot {shot.idx}: corte sem re-encodar cai no "
            "keyframe mais próximo — o começo do clipe pode trazer até 2s da "
            "cena anterior. Ligue a re-codificação pra corte exato.",
            flush=True,
        )
        stream = ffmpeg.input(str(video_path), ss=shot.start, to=shot.end).output(
            str(out_file),
            c="copy",
            format="mp4",
            avoid_negative_ts="make_zero",
            loglevel="error",
        )
    run_ffmpeg_hidden(stream)


def cut_all_shots_segment(
    video_path: str | Path,
    shots: list[ShotBounds],
    shots_dir: Path,
    fps: float,
    use_nvenc: bool,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict[int, Path]:
    """Corta TODOS os shots numa passada só (segment muxer do ffmpeg).

    O caminho antigo dispara um ffmpeg por shot: medido nesta máquina, cada
    processo gasta 169 ms só pra subir, abrir e indexar o MKV antes de
    decodificar o primeiro frame útil — 361 shots × 169 ms = 61 s de imposto
    puro, com o arquivo sendo reaberto e re-indexado 361 vezes. Aqui o vídeo
    é lido UMA vez e o muxer troca de arquivo de saída nos tempos pedidos.

    `-force_key_frames` nos MESMOS tempos do `-segment_times` é o que mantém
    o corte exato no frame: sem ele o segment cai no keyframe mais próximo
    (o GOP daqui é 2.002 s — até 2 segundos de invasão da cena vizinha).

    Detalhe que obriga a mapear de volta: cenas curtas demais são descartadas
    lá na detecção, então a lista de shots tem BURACOS. Os segmentos saem
    numerados em sequência cobrindo o episódio inteiro, inclusive os pedaços
    descartados — então cada segmento é casado com o shot pelo tempo de
    início e os não reclamados são apagados.

    Devolve {shot.idx: arquivo}. Vazio = deu errado, o chamador cai no
    caminho por shot.
    """
    if not shots:
        return {}
    shots_dir.mkdir(parents=True, exist_ok=True)
    half = 0.5 / max(fps, 1.0)

    # Fronteiras = todos os inícios e fins, ordenados e sem duplicatas
    # (shots contíguos compartilham fronteira).
    bounds: list[float] = []
    for s in shots:
        for t in (float(s.start), float(s.end)):
            if not bounds or t - bounds[-1] > half:
                bounds.append(t)
            elif t > bounds[-1]:
                bounds[-1] = t
    bounds = sorted(set(round(b, 3) for b in bounds))
    # Meio frame ANTES da fronteira, mesma margem do modo por cena. Sem ela,
    # o arredondamento de timestamp do MKV (milissegundos) faz o primeiro
    # frame da cena seguinte cair no clipe anterior — medido, vazava em 28%
    # dos pares. Com a margem, o corte cai entre os dois frames: o último
    # frame legítimo fica, o intruso vai pro arquivo certo.
    cut_times = [b - half for b in bounds if b - half > half]
    if not cut_times:
        return {}

    stage = shots_dir / "_segment_tmp"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    times_str = ",".join(f"{t:.3f}" for t in cut_times)
    vcodec = (
        ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
        if use_nvenc
        else ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20"]
    )
    cmd = [
        str(ffmpeg_binary()), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_path),
        *vcodec,
        "-c:a", "aac",
        "-force_key_frames", times_str,
        "-f", "segment",
        "-segment_times", times_str,
        "-reset_timestamps", "1",
        "-segment_format", "mp4",
        str(stage / "%05d.mp4"),
    ]

    # Progresso: o muxer não reporta por shot, então a contagem de arquivos
    # prontos na pasta de trabalho serve de régua (é o que o usuário vê).
    done_flag = {"stop": False}
    total = len(shots)

    def watch() -> None:
        seen = 0
        while not done_flag["stop"]:
            try:
                n = sum(1 for _ in stage.glob("*.mp4"))
            except OSError:
                n = seen
            if n > seen and on_progress:
                seen = n
                try:
                    on_progress(min(n, total), total, 0)
                except Exception:
                    done_flag["stop"] = True
                    return
            time.sleep(0.35)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_no_window_flag(),
        )
    finally:
        done_flag["stop"] = True
        watcher.join(timeout=1.0)

    segs = sorted(stage.glob("*.mp4"))
    if proc.returncode != 0 or len(segs) < len(shots):
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        print(
            f"[CorteCenas] Corte em passada única não deu certo "
            f"({len(segs)} segmentos pra {len(shots)} cenas"
            + (f"; {err}" if err else "") + ") — usando o modo por cena.",
            flush=True,
        )
        shutil.rmtree(stage, ignore_errors=True)
        return {}

    # Segmento i cobre [bounds_completo[i], bounds_completo[i+1]).
    seg_starts = [0.0] + [t + half for t in cut_times]   # desfaz a margem
    out: dict[int, Path] = {}
    for s in shots:
        # casa pelo início (tolerância de meio frame)
        best_i, best_d = None, half + 1e-6
        for i, t in enumerate(seg_starts):
            d = abs(t - float(s.start))
            if d <= best_d:
                best_i, best_d = i, d
        if best_i is None or best_i >= len(segs):
            continue
        dest = shots_dir / f"{s.idx:04d}.mp4"
        try:
            if dest.exists():
                dest.unlink()
            segs[best_i].replace(dest)
            out[s.idx] = dest
        except OSError:
            continue

    shutil.rmtree(stage, ignore_errors=True)
    if len(out) < len(shots):
        print(
            f"[CorteCenas] Passada única cobriu {len(out)}/{len(shots)} cenas — "
            "as que faltaram saem no modo por cena.",
            flush=True,
        )
    return out


def _offsets(n_frames: int) -> list[float]:
    """Frações da duração do shot onde amostrar (1/4, 1/2, 3/4 com n=3)."""
    if n_frames <= 1:
        return [0.5]
    return [(i + 1) / (n_frames + 1) for i in range(n_frames)]


def extract_keyframes(
    video_path: str | Path,
    shot: ShotBounds,
    out_dir: Path,
    n_frames: int = 3,
    clip_path: Path | None = None,
) -> list[Path]:
    """Sample N frames uniformly across the shot and save as JPGs.

    `clip_path` é o clipe DESTE shot, recém-cortado: quando existe, os frames
    saem dele em vez do vídeo-fonte. Medido: 53s -> 21s no episódio inteiro.
    O motivo é o custo do salto — no MKV de 1.4 GB cada `POS_FRAMES` obriga o
    decodificador a voltar até o keyframe anterior (2s = 48 frames) e decodar
    até o alvo, 1083 vezes; no clipe de 3 MB que acabou de ser escrito ao lado
    (e ainda está quente no cache do SO) o salto é trivial. Os pixels são os
    mesmos: o clipe é exatamente a janela do shot, então a fração relativa cai
    no mesmo frame — validado quadro a quadro antes de virar padrão.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    offsets = _offsets(n_frames)

    # Caminho rápido: ler do clipe (posição relativa dentro dele).
    if clip_path is not None and clip_path.exists() and clip_path.stat().st_size > 0:
        cap = cv2.VideoCapture(str(clip_path))
        if cap.isOpened():
            n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if n_total > 0:
                paths: list[Path] = []
                for k, off in enumerate(offsets):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, min(int(n_total * off), n_total - 1))
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    out = out_dir / f"{shot.idx:04d}_{k}.jpg"
                    # SÓ ENTRA NA LISTA SE GRAVOU. Isto chamava `cv2.imwrite`
                    # e appendava o caminho sem olhar o retorno — e o imwrite
                    # devolve False (sem exceção) quando não consegue
                    # escrever, que é o que acontece com acento na pasta.
                    if not gravar_imagem(out, frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                        _falhou_a_escrita(out)
                        continue
                    paths.append(out)
                cap.release()
                if len(paths) == len(offsets):
                    return paths
                # Clipe curto/corrompido: cai pro caminho antigo abaixo.
            else:
                cap.release()

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    paths = []

    for k, off in enumerate(offsets):
        t = shot.start + shot.duration * off
        frame_idx = int(t * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            continue
        out = out_dir / f"{shot.idx:04d}_{k}.jpg"
        if not gravar_imagem(out, frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            _falhou_a_escrita(out)
            continue
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
    """Cut shots and extract keyframes, several shots at a time.

    Each shot is an independent (ffmpeg cut + cv2 keyframes) work unit, so
    they run in a thread pool: NVENC when the GPU has it (3 workers, safe for
    every session-limited GeForce), libx264 otherwise (4 workers). One shot
    at a time on CPU was ~86% of the whole pipeline's wall clock.

    If `skip_existing` is True, shots whose .mp4 is already on disk (non-empty)
    are not re-encoded, and keyframes already present are not re-extracted.

    `on_progress` is called from THIS thread as results complete (completion
    order, monotonic count) — raising from it (PipelineCancelled) cancels all
    queued shots; in-flight ffmpeg calls finish into the cache.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)
    keyframes_dir.mkdir(parents=True, exist_ok=True)

    # fps do vídeo, sondado uma vez: o corte usa meia duração de frame como
    # margem pra não deixar o primeiro frame do shot seguinte vazar pro clipe.
    probe = cv2.VideoCapture(str(video_path))
    video_fps = probe.get(cv2.CAP_PROP_FPS) if probe.isOpened() else 0.0
    probe.release()
    if not video_fps or video_fps <= 0 or video_fps != video_fps:
        video_fps = 24.0

    total = len(shots)
    # Shared, mutated on NVENC runtime failure (driver/session hiccup): the
    # remaining shots silently switch to libx264. Benign race — worst case a
    # couple extra NVENC attempts before every worker sees the flag.
    enc_state = {"nvenc": reencode and nvenc_available()}
    workers = _NVENC_WORKERS if enc_state["nvenc"] else _CPU_WORKERS
    workers = max(1, min(workers, os.cpu_count() or 4, total or 1))

    # Caminho rápido: se a maior parte dos clipes ainda não existe, corta tudo
    # numa passada só (o vídeo é lido uma vez em vez de N). Vale a pena a
    # partir de um punhado de cenas; com poucas, o modo por cena já é ótimo e
    # evita reprocessar o episódio inteiro por causa de 3 clipes.
    if reencode and total >= 20:
        missing = [
            s for s in shots
            if not (skip_existing
                    and (shots_dir / f"{s.idx:04d}.mp4").exists()
                    and (shots_dir / f"{s.idx:04d}.mp4").stat().st_size > 0)
        ]
        if len(missing) >= max(20, total // 2):
            cut_all_shots_segment(
                video_path, shots, shots_dir, video_fps,
                enc_state["nvenc"], on_progress,
            )

    def process(shot: ShotBounds) -> tuple[ShotBounds, Path, list[Path], bool] | None:
        out_file = shots_dir / f"{shot.idx:04d}.mp4"
        expected_kfs = [keyframes_dir / f"{shot.idx:04d}_{k}.jpg" for k in range(keyframes_per_shot)]

        have_cut = out_file.exists() and out_file.stat().st_size > 0
        have_kfs = all(p.exists() and p.stat().st_size > 0 for p in expected_kfs)

        if not (skip_existing and have_cut):
            try:
                cut_shot(video_path, shot, out_file, reencode=reencode,
                         use_nvenc=enc_state["nvenc"], fps=video_fps)
            except ffmpeg.Error:
                if enc_state["nvenc"]:
                    enc_state["nvenc"] = False
                    print(
                        f"[CorteCenas] NVENC falhou no shot {shot.idx} — "
                        "continuando na CPU (libx264)",
                        flush=True,
                    )
                    try:
                        cut_shot(video_path, shot, out_file, reencode=reencode,
                                 use_nvenc=False, fps=video_fps)
                    except ffmpeg.Error:
                        return None
                else:
                    return None

        if skip_existing and have_kfs:
            kfs = expected_kfs
        else:
            kfs = extract_keyframes(
                video_path, shot, keyframes_dir,
                n_frames=keyframes_per_shot,
                clip_path=out_file,   # o clipe deste shot acabou de ser escrito
            )

        return shot, out_file, kfs, (skip_existing and have_cut and have_kfs)

    indexed: list[tuple[ShotBounds, Path, list[Path]] | None] = [None] * total
    done = 0
    skipped = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, shot): i for i, shot in enumerate(shots)}
        try:
            for fut in as_completed(futures):
                res = fut.result()
                done += 1
                if res is not None:
                    shot, out_file, kfs, was_skipped = res
                    indexed[futures[fut]] = (shot, out_file, kfs)
                    if was_skipped:
                        skipped += 1
                if on_progress:
                    on_progress(done, total, skipped)
        except BaseException:
            # PipelineCancelled (or anything else) — drop everything queued;
            # shots already encoding finish into the cache and get reused on
            # the next run.
            pool.shutdown(wait=False, cancel_futures=True)
            raise

    # Original shot order, minus the ones ffmpeg couldn't cut.
    #
    # E as que ele NÃO conseguiu cortar são contadas em voz alta. Este filtro
    # engolia em silêncio todo shot que o `process` devolveu None: disco
    # cheio, arquivo travado por outro programa, codec que o ffmpeg recusa.
    # Ninguém comparava `len(cut_results)` com `len(shots)`, então 40 cenas
    # perdidas no meio de 300 chegavam ao fim como "análise pronta".
    #
    # Não levanta: cortar 260 de 300 ainda é resultado útil. Quem chamou
    # compara `len(retorno)` com `len(shots)` — a conta já está na mão dele,
    # não vale guardar isso num estado global.
    saida = [r for r in indexed if r is not None]
    if len(saida) < total:
        print(f"[CorteCenas] ATENÇÃO: {total - len(saida)} de {total} cena(s) "
              f"o ffmpeg não conseguiu cortar (disco cheio? arquivo em uso?) "
              f"— reanalisar tenta de novo só essas", flush=True)
    return saida
