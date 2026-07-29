# -*- coding: utf-8 -*-
"""O CRONÔMETRO: mede a análise de ponta a ponta, fria e quente, ISOLADA.

Uso:  python benchmarks/run_perf.py <video> "<anime>" <S> <E>
              [--runs 2] [--keep] [--golden golden/mushoku_s03e02.json]

Com --golden ele também dá NOTA: roda o mesmo cálculo do juiz sobre o
resultado de cada rodada. É o único jeito de validar mudanças que só
aparecem em rodada FRIA (fonte dos keyframes, corte, detecção) — o
run_bench.py roda quente, em cima do cache, e por isso não enxerga nenhuma
delas.

Por que existe: o juiz (run_bench.py) protege a QUALIDADE; este protege o TEMPO.
Toda otimização precisa mostrar o antes/depois aqui, no mesmo vídeo, na mesma
máquina — "ficou mais rápido" sem número não entra.

Isolamento (a regra da casa: teste destrutivo nunca toca o que é do usuário):
tudo roda num cache e num Output temporários; as fotos de referência do anime são
COPIADAS pra lá (14 MB) pra não pagar rede nem arriscar escrita nas refs reais.
O banco real, o Output real e a curadoria do usuário ficam intocados.

A 1ª rodada é FRIA (corta o vídeo, roda YOLO+CLIP em tudo); as seguintes são
QUENTES (cache de features cheio) — que é o caso do dia a dia, reanalisar
depois de mexer num ajuste."""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config
from app.pipeline import Pipeline
from app.video_ingest import EpisodeInfo

STAGE_LABELS = {
    "parse": "Lendo o vídeo",
    "detect_shots": "Detectando cenas",
    "cut_shots": "Cortando clipes",
    "fetch_characters": "Buscando personagens (rede)",
    "download_refs": "Baixando referências",
    "embed_refs": "Embeddings das referências",
    "analyze_shots": "Analisando shots (YOLO+CLIP)",
    "second_pass": "Resgate + grupos",
    "ai_review": "Revisão IA",
    "organize": "Organizando saída",
}


def _isolated_config(sandbox: Path) -> Config:
    """Config apontando pra um cache/Output descartáveis, com as refs copiadas."""
    cfg = Config.load()
    real_refs = Path(cfg.cache_dir) / "anime_db"
    cache = sandbox / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if real_refs.exists() and not (cache / "anime_db").exists():
        shutil.copytree(real_refs, cache / "anime_db")
    cfg.cache_dir = str(cache)
    cfg.output_dir = str(sandbox / "Output")
    cfg.ensure_dirs()
    return cfg


def _score(pipe: Pipeline, episode_id: int, golden: dict) -> float:
    """MACRO F1 do resultado desta rodada contra o gabarito — mesmo cálculo do
    juiz, aplicado ao banco do sandbox. É o que permite validar mudanças que
    só aparecem em rodada FRIA (keyframes, corte, detecção): o run_bench roda
    quente e por isso não enxerga nada disso."""
    by_shot = pipe.db.assignments_for_episode(episode_id)
    idx_by_id = {s["id"]: s["idx"] for s in pipe.db.shots_for_episode(episode_id)}
    pred: dict[int, set] = {}
    for sid, assigns in by_shot.items():
        pred[idx_by_id[sid]] = {a["name"] for a in assigns}
    gold = {int(k): set(v) for k, v in golden["per_shot"].items()}
    f1s = []
    for name in golden["characters"]:
        tp = sum(1 for i, g in gold.items() if name in g and name in pred.get(i, set()))
        fn = sum(1 for i, g in gold.items() if name in g and name not in pred.get(i, set()))
        fp = sum(1 for i, p in pred.items() if name in p and name not in gold.get(i, set()))
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)
        print(f"    {name:<26} P {prec:.2f}  R {rec:.2f}  F1 {f1s[-1]:.2f}")
    return sum(f1s) / len(f1s) if f1s else 0.0


def _run_once(cfg: Config, info: EpisodeInfo, label: str, golden: dict | None = None) -> dict:
    pipe = Pipeline(cfg)
    t0 = time.time()
    result = pipe.run(info, on_progress=lambda s, f, m: None, ai_review_ambiguous=False)
    total = time.time() - t0
    timings = {}
    tj = Path(result.episode_root) / "metadata" / "timings.json"
    if tj.exists():
        timings = json.loads(tj.read_text(encoding="utf-8")).get("stages", {})
    print(f"\n=== {label} — {total:.1f}s ({result.total_shots} shots) ===")
    for stage, secs in sorted(timings.items(), key=lambda kv: -kv[1]):
        if secs < 0.05:
            continue
        pct = 100 * secs / total if total else 0
        bar = "█" * max(1, int(pct / 2))
        print(f"  {STAGE_LABELS.get(stage, stage):<28} {secs:>7.2f}s {pct:>5.1f}% {bar}")
    macro = None
    if golden:
        print("  --- qualidade contra o gabarito ---")
        macro = _score(pipe, result.episode_id, golden)
        veredito = "OK" if macro >= 0.999 else "REGREDIU"
        print(f"    {'MACRO F1':<26} {macro:.3f}   <<< {veredito}")
    return {
        "label": label, "total": total, "stages": timings,
        "shots": result.total_shots, "macro_f1": macro,
    }


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__)
        return 1
    video = Path(sys.argv[1])
    anime, season, episode = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    runs = 2
    if "--runs" in sys.argv:
        runs = int(sys.argv[sys.argv.index("--runs") + 1])
    keep = "--keep" in sys.argv
    golden = None
    if "--golden" in sys.argv:
        gp = Path(sys.argv[sys.argv.index("--golden") + 1])
        if not gp.is_absolute():
            gp = Path(__file__).parent / gp
        golden = json.loads(gp.read_text(encoding="utf-8"))
    if not video.exists():
        print(f"Vídeo não encontrado: {video}")
        return 1

    sandbox = Path(__file__).parent / "_perf_sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    cfg = _isolated_config(sandbox)
    info = EpisodeInfo(anime=anime, season=season, episode=episode, source=video)
    print(f"Sandbox: {sandbox}\nVídeo:   {video.name}")

    results = [_run_once(cfg, info, "RODADA 1 (FRIA)", golden)]
    for i in range(2, runs + 1):
        results.append(_run_once(cfg, info, f"RODADA {i} (QUENTE)", golden))

    print("\n" + "=" * 62)
    print(f"{'':<28} {'fria':>9} {'quente':>9}")
    warm = [r for r in results[1:]]
    warm_of = lambda k: (
        statistics.median([w["stages"].get(k, 0.0) for w in warm]) if warm else 0.0
    )
    for stage in STAGE_LABELS:
        cold = results[0]["stages"].get(stage, 0.0)
        w = warm_of(stage)
        if cold < 0.05 and w < 0.05:
            continue
        print(f"{STAGE_LABELS[stage]:<28} {cold:>8.2f}s {w:>8.2f}s")
    w_total = statistics.median([w["total"] for w in warm]) if warm else 0.0
    print(f"{'TOTAL':<28} {results[0]['total']:>8.1f}s {w_total:>8.1f}s")

    out = Path(__file__).parent / "perf_last.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetalhes: {out}")
    if not keep:
        shutil.rmtree(sandbox, ignore_errors=True)
        print("Sandbox apagado (use --keep pra inspecionar a saída).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
