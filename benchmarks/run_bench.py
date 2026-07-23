# -*- coding: utf-8 -*-
"""O JUIZ: roda a análise nos episódios-gabarito e pontua contra a verdade.

Uso:  python benchmarks/run_bench.py golden/mushoku_s03e02.json [golden/outro.json ...]

Toda mudança de matching passa por aqui ANTES de virar release — precisão e
cobertura por personagem, com números, não com "acho que melhorou". Requer
os cortes/features do episódio em cache (reanálise quente, segundos)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config
from app.pipeline import Pipeline
from app.video_ingest import EpisodeInfo


def score_episode(pipe: Pipeline, golden: dict) -> dict:
    info = EpisodeInfo(
        anime=golden["anime_query"],
        season=golden["season"],
        episode=golden["episode"],
        source=Path(golden["source_file"]),
    )
    t0 = time.time()
    r = pipe.run(info, on_progress=lambda s, f, m: None, ai_review_ambiguous=False)
    elapsed = time.time() - t0

    by_shot = pipe.db.assignments_for_episode(r.episode_id)
    shots = pipe.db.shots_for_episode(r.episode_id)
    idx_by_id = {s["id"]: s["idx"] for s in shots}
    pred: dict[int, set[str]] = {}
    for sid, assigns in by_shot.items():
        pred[idx_by_id[sid]] = {a["name"] for a in assigns}

    gold: dict[int, set[str]] = {
        int(k): set(v) for k, v in golden["per_shot"].items()
    }
    chars = golden["characters"]
    rows = []
    for name in chars:
        tp = sum(1 for i, g in gold.items() if name in g and name in pred.get(i, set()))
        fn = sum(1 for i, g in gold.items() if name in g and name not in pred.get(i, set()))
        fp = sum(1 for i, p in pred.items() if name in p and name not in gold.get(i, set()))
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        rows.append((name, tp, fp, fn, prec, rec, f1))
    extras = sorted(
        {n for p in pred.values() for n in p} - set(chars)
    )
    return {
        "title": f"{golden['anime']} S{golden['season']:02d}E{golden['episode']:02d}",
        "elapsed": elapsed,
        "rows": rows,
        "extras": extras,
        "macro_f1": sum(r[6] for r in rows) / len(rows) if rows else 0.0,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cfg = Config.load()
    pipe = Pipeline(cfg)
    worst = 1.0
    for arg in sys.argv[1:]:
        golden = json.loads(
            (Path(__file__).parent / arg).read_text(encoding="utf-8")
            if not Path(arg).is_absolute() else Path(arg).read_text(encoding="utf-8")
        )
        res = score_episode(pipe, golden)
        print(f"\n=== {res['title']}  ({res['elapsed']:.0f}s) ===")
        print(f"{'personagem':<28} {'TP':>4} {'FP':>4} {'FN':>4} {'prec':>6} {'rec':>6} {'F1':>6}")
        for name, tp, fp, fn, prec, rec, f1 in res["rows"]:
            print(f"{name:<28} {tp:>4} {fp:>4} {fn:>4} {prec:>6.2f} {rec:>6.2f} {f1:>6.2f}")
        print(f"{'MACRO F1':<28} {'':>4} {'':>4} {'':>4} {'':>6} {'':>6} {res['macro_f1']:>6.2f}")
        if res["extras"]:
            print(f"personagens fora do gabarito: {', '.join(res['extras'])}")
        worst = min(worst, res["macro_f1"])
    print(f"\nPIOR MACRO F1: {worst:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
