from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from scenedetect import ContentDetector, SceneManager, open_video

# O PySceneDetect já trabalha internamente em 256px de largura (ele reduz
# sozinho: DEFAULT_MIN_WIDTH=256, auto_downscale=True). Ou seja, a conta da
# detecção é barata — o custo todo é DECODIFICAR ~34 mil frames 1080p pelo
# OpenCV e empurrá-los por uma fila do Python. Alimentar o MESMO detector
# com frames já reduzidos, vindos do ffmpeg que já vem embutido, foi medido
# entre 3,5x e 7,4x mais rápido, sem mudar a matemática da decisão.
_TARGET_WIDTH = 256


@dataclass
class ShotBounds:
    idx: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _probe(video_path: str | Path) -> dict | None:
    """largura/altura/fps/nº de frames pelo ffprobe embutido."""
    from .ffmpeg_locate import ffprobe_binary

    try:
        proc = subprocess.run(
            [
                str(ffprobe_binary()), "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,avg_frame_rate,nb_frames",
                "-show_entries", "format=duration",
                "-of", "json", str(video_path),
            ],
            capture_output=True, timeout=60,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
        st = (data.get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        num, _, den = (st.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        dur = float((data.get("format") or {}).get("duration") or 0.0)
        if w <= 0 or h <= 0 or fps <= 0:
            return None
        return {"w": w, "h": h, "fps": fps, "duration": dur}
    except Exception:
        return None


def _detect_via_ffmpeg(
    video_path: str | Path,
    threshold: float,
    on_progress: Callable[[float], None] | None,
) -> list[tuple[float, float]] | None:
    """Roda o MESMO ContentDetector, mas com o ffmpeg entregando os frames
    já reduzidos por um pipe. Devolve [(início, fim)] em segundos, ou None
    se qualquer coisa não bater (o chamador cai no caminho de sempre)."""
    from .ffmpeg_locate import ffmpeg_binary

    info = _probe(video_path)
    if not info:
        return None
    # Mesma regra de redução do PySceneDetect: divisor inteiro até 256px.
    factor = max(1, round(info["w"] / _TARGET_WIDTH))
    w = info["w"] // factor
    h = info["h"] // factor
    w -= w % 2
    h -= h % 2
    if w < 64 or h < 36:
        return None
    frame_bytes = w * h * 3
    fps = info["fps"]
    total_frames = int(info["duration"] * fps) if info["duration"] else 0

    det = ContentDetector(threshold=threshold)
    cuts: list[int] = []
    cmd = [
        str(ffmpeg_binary()), "-hide_banner", "-loglevel", "error",
        "-i", str(video_path), "-an", "-sn",
        # BICÚBICA de propósito: é a interpolação que o SceneManager usa pra
        # reduzir (Interpolation.CUBIC é o padrão dele). Com bilinear rápida
        # o fiscal de fronteira reprovou — mesmo detector e mesmo threshold,
        # mas 7% dos cortes mudavam de lugar só por causa do filtro.
        "-vf", f"scale={w}:{h}", "-sws_flags", "bicubic",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=frame_bytes * 4,
        creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
    )
    n = 0
    try:
        read = proc.stdout.read
        while True:
            buf = read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            for c in det.process_frame(n, frame) or ():
                cuts.append(int(c))
            n += 1
            if on_progress is not None and total_frames and (n & 0x7F) == 0:
                on_progress(min(n / total_frames, 1.0))
    except Exception:
        proc.kill()
        return None
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait(timeout=10)
    if proc.returncode not in (0, None) or n < 10:
        return None
    for c in det.post_process(n) or ():
        cuts.append(int(c))

    # Cortes -> intervalos [início, fim) em segundos.
    edges = [0] + sorted(set(cuts)) + [n]
    out: list[tuple[float, float]] = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        if b > a:
            out.append((a / fps, b / fps))
    return out or None


def detect_shots(
    video_path: str | Path,
    threshold: float = 27.0,
    min_seconds: float = 0.6,
    on_progress: Callable[[float], None] | None = None,
    fast: bool = False,
) -> list[ShotBounds]:
    if fast:
        quick = _detect_via_ffmpeg(video_path, threshold, on_progress)
        if quick is not None:
            return _to_shots(
                quick, min_seconds, on_progress, fallback_duration=quick[-1][1]
            )
        print("[CorteCenas] Detecção rápida não deu certo — usando a normal.")

    video = open_video(str(video_path))
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    # detect_scenes blocks for the whole episode (minutes). The per-cut
    # callback (fires every few seconds of video) feeds real progress to the
    # UI — and gives the cancel button a place to land mid-detection.
    callback = None
    if on_progress is not None:
        total_frames = max(int(video.duration.get_frames() or 0), 1)

        def callback(_image, frame_num: int) -> None:
            on_progress(min(frame_num / total_frames, 1.0))

    sm.detect_scenes(video, show_progress=False, callback=callback)
    scenes = [(s.get_seconds(), e.get_seconds()) for s, e in sm.get_scene_list()]
    return _to_shots(
        scenes, min_seconds, on_progress,
        fallback_duration=video.duration.get_seconds(),
    )


def apply_merges(
    shots: list[ShotBounds], merges: list[dict]
) -> list[ShotBounds]:
    """Colapsa em UMA cena cada grupo de shots que cai dentro de uma janela
    de junção pedida pelo usuário.

    O detector às vezes parte uma cena em duas (um flash, um corte de
    câmera, um fade). As janelas vêm em SEGUNDOS justamente pra sobreviver
    a uma redetecção: se um corte andar dois frames, a janela continua
    pegando os mesmos pedaços. Um shot entra na janela quando o CENTRO dele
    está dentro — assim uma fronteira que se moveu um pouco não escapa.

    A cena juntada HERDA o número da primeira do grupo, e ninguém é
    renumerado: a numeração fica com buracos, e é de propósito. Renumerar do
    zero deslocaria todas as cenas seguintes — e curadoria, bloqueios e
    gabarito são guardados POR NÚMERO. Juntar duas cenas não pode reescrever
    a identidade das outras trezentas.
    """
    if not merges or not shots:
        return shots
    janelas = sorted(
        ((float(m["start"]), float(m["end"])) for m in merges), key=lambda t: t[0]
    )
    out: list[ShotBounds] = []
    grupo: dict[int, list[ShotBounds]] = {}
    for s in shots:
        centro = (s.start + s.end) / 2.0
        alvo = None
        for i, (a, b) in enumerate(janelas):
            if a - 1e-6 <= centro <= b + 1e-6:
                alvo = i
                break
        if alvo is None:
            out.append(s)
        else:
            grupo.setdefault(alvo, []).append(s)
    for membros in grupo.values():
        if not membros:
            continue
        primeira = min(membros, key=lambda m: m.start)
        out.append(
            ShotBounds(
                idx=primeira.idx,      # herda o número — sem renumerar ninguém
                start=primeira.start,
                end=max(m.end for m in membros),
            )
        )
    out.sort(key=lambda s: s.start)
    return out


def _to_shots(
    scenes: list[tuple[float, float]],
    min_seconds: float,
    on_progress: Callable[[float], None] | None,
    fallback_duration: float,
) -> list[ShotBounds]:
    """Lista de (início, fim) em segundos -> shots numerados.

    Cena curta demais é FUNDIDA na anterior, não jogada fora: descartar
    abria um buraco no episódio — aquele pedaço de vídeo não existia em
    clipe nenhum da saída. (Na prática quase nunca dispara: o
    ContentDetector já respeita um mínimo de ~15 frames antes de nos
    entregar a lista; medido, 0 ocorrências em Mushoku e Slime. Mas quando
    disparava, sumia vídeo em silêncio.)

    Os dois caminhos de detecção (ffmpeg e OpenCV) passam por aqui, então a
    regra de montagem é literalmente a mesma nos dois.
    """
    shots: list[ShotBounds] = []
    idx = 0
    for start, end in scenes:
        if end - start < min_seconds:
            if shots:
                shots[-1].end = end          # cola no shot anterior
            else:
                shots.append(ShotBounds(idx=idx, start=start, end=end))
                idx += 1
            continue
        if shots and shots[-1].end == start and shots[-1].duration < min_seconds:
            shots[-1].end = end              # o "pendente" do começo absorve
            continue
        shots.append(ShotBounds(idx=idx, start=start, end=end))
        idx += 1

    if not shots:
        shots = [ShotBounds(idx=0, start=0.0, end=fallback_duration)]

    if on_progress is not None:
        on_progress(1.0)
    return shots
