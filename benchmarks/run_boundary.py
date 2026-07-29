# -*- coding: utf-8 -*-
"""O FISCAL DE FRONTEIRA: prova que a detecção de cenas não mudou os cortes.

Uso:  python benchmarks/run_boundary.py <video> [<video2> ...]

Por que existe (armadilha real, custou uma auditoria pra achar): o juiz
(run_bench.py) compara personagem por personagem usando o NÚMERO da cena
como chave. Se a detecção passar a achar uma cena a mais no minuto 2, todos
os números depois disso andam uma casa e o juiz acusa catástrofe — um alarme
falso gigante que faria reverter uma mudança boa. Pior: os bloqueios da
curadoria manual também são guardados por número de cena.

Então mudança em detect_shots se valida AQUI primeiro: comparando os tempos
de corte contra os que já estão salvos em Output/<anime>/<SxxExx>/metadata/,
com tolerância de 2 frames. Só depois de passar aqui é que faz sentido
regravar o gabarito e rodar o juiz."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config
from app.shot_detection import detect_shots

TOL_FRAMES = 2.0
ASSUME_FPS = 23.976


def _saved_bounds(video: Path) -> tuple[list[float], str] | None:
    """Acha o shots.json de um episódio já analisado a partir do vídeo."""
    cfg = Config.load()
    for shots_json in sorted(cfg.output_path.glob("*/*/metadata/shots.json")):
        try:
            data = json.loads(shots_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data:
            continue
        ep = shots_json.parent.parent
        # casa pelo nome do arquivo fonte guardado no banco, senão pelo
        # número de cenas + duração (heurística boa o bastante aqui)
        return [float(s["start"]) for s in data], str(ep)
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    worst_ok = True
    for arg in sys.argv[1:]:
        video = Path(arg)
        if not video.exists():
            print(f"vídeo não encontrado: {video}")
            return 1
        found = _saved_bounds(video)
        if found is None:
            print(f"sem shots.json de referência pra {video.name} — analise o episódio uma vez antes.")
            return 1
        ref, ep = found

        print(f"\n=== {video.name} ===")
        print(f"referência: {ep}  ({len(ref)} cenas)")
        t0 = time.time()
        shots = detect_shots(video)
        elapsed = time.time() - t0
        got = [s.start for s in shots]
        print(f"detecção atual: {len(got)} cenas em {elapsed:.1f}s")

        tol = TOL_FRAMES / ASSUME_FPS
        matched, deltas = 0, []
        for t in ref:
            best = min((abs(t - g), g - t) for g in got) if got else (99.0, 0.0)
            if best[0] <= tol:
                matched += 1
                deltas.append(best[1])
        extra = len(got) - matched
        missing = len(ref) - matched
        pct = 100.0 * matched / max(len(ref), 1)
        bias = sum(deltas) / len(deltas) if deltas else 0.0
        exact = sum(1 for d in deltas if abs(d) < 1e-6)

        print(f"  casaram (±{TOL_FRAMES:.0f} frames): {matched}/{len(ref)} = {pct:.1f}%")
        print(f"  exatas (delta 0): {exact}  |  viés médio: {bias * 1000:+.1f} ms")
        print(f"  fronteiras a mais: {extra}  |  perdidas: {missing}")

        ok = pct >= 95.0 and abs(bias) < tol / 2 and extra <= max(2, len(ref) * 0.02)
        print(f"  VEREDITO: {'APROVADO' if ok else 'REPROVADO'}")
        if not ok:
            worst_ok = False
            print(
                "  (Reprovar aqui significa que os clipes mudariam de lugar: o\n"
                "   gabarito e os bloqueios da curadoria, que são guardados por\n"
                "   NÚMERO de cena, apontariam pro shot errado.)"
            )
    return 0 if worst_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
