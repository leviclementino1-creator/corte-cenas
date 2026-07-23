# -*- coding: utf-8 -*-
"""Congela o estado ATUAL de um episódio como gabarito de benchmark.

Uso:  python benchmarks/make_golden.py "Mushoku" 3 2 golden/mushoku_s03e02.json

O gabarito é o resultado validado (batismos + curadoria do usuário +
conferência visual) — a régua contra a qual toda mudança de matching passa
a ser medida. Só entram personagens com um mínimo de cenas (ruído de 1-2
cenas não vira verdade absoluta)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config
from app.storage.db import Database

MIN_SHOTS_GOLDEN = 8   # personagem com menos cenas que isso fica fora do gabarito


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__)
        return 1

    # Modo alternativo: gabarito direto do metadata/shots.json de um episódio
    # (quando o banco foi apagado mas a pasta de Output sobreviveu):
    #   make_golden.py --from-shots <shots.json> <query> <S> <E> <source> <out>
    if sys.argv[1] == "--from-shots":
        shots_json, title_like, season, episode, source, out = (
            Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]),
            int(sys.argv[5]), sys.argv[6], Path(sys.argv[7]),
        )
        shots = json.loads(shots_json.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for s in shots:
            for c in s.get("characters") or []:
                counts[c["name"]] = counts.get(c["name"], 0) + 1
        keep = {n for n, c in counts.items() if c >= MIN_SHOTS_GOLDEN}
        per_shot = {}
        for s in shots:
            names = sorted(
                c["name"] for c in (s.get("characters") or [])
                if c["name"] in keep
            )
            if names:
                # shots.json usa shot_id string ("0012"); o gabarito usa idx int
                per_shot[str(int(s["shot_id"]))] = names
        payload = {
            "anime": title_like,
            "anime_query": title_like,
            "season": season,
            "episode": episode,
            "source_file": source,
            "characters": sorted(keep),
            "char_counts": {n: counts[n] for n in sorted(keep)},
            "per_shot": per_shot,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Gabarito salvo (de shots.json): {out}")
        print(f"  personagens: {', '.join(f'{n}({counts[n]})' for n in sorted(keep))}")
        return 0

    title_like, season, episode, out = (
        sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])
    )
    cfg = Config.load()
    db = Database(Path(cfg.cache_dir) / "index.db")
    with db.connect() as c:
        ep = c.execute(
            """SELECT e.id, e.source_file, a.title FROM episode e
               JOIN anime a ON a.id = e.anime_id
               WHERE a.title LIKE ? AND e.season=? AND e.episode=?""",
            (f"%{title_like}%", season, episode),
        ).fetchone()
    if not ep:
        print(f"Episódio não encontrado no banco: {title_like} S{season}E{episode}")
        return 1

    by_shot = db.assignments_for_episode(ep["id"])
    shots = db.shots_for_episode(ep["id"])
    idx_by_id = {s["id"]: s["idx"] for s in shots}

    counts: dict[str, int] = {}
    for assigns in by_shot.values():
        for a in assigns:
            counts[a["name"]] = counts.get(a["name"], 0) + 1
    keep = {n for n, c in counts.items() if c >= MIN_SHOTS_GOLDEN}
    dropped = {n: c for n, c in counts.items() if n not in keep}

    per_shot: dict[str, list[str]] = {}
    for sid, assigns in by_shot.items():
        names = sorted(a["name"] for a in assigns if a["name"] in keep)
        if names:
            per_shot[str(idx_by_id[sid])] = names

    payload = {
        "anime": ep["title"],
        "anime_query": title_like,
        "season": season,
        "episode": episode,
        "source_file": ep["source_file"],
        "characters": sorted(keep),
        "char_counts": {n: counts[n] for n in sorted(keep)},
        "per_shot": per_shot,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Gabarito salvo: {out}")
    print(f"  personagens: {', '.join(f'{n}({counts[n]})' for n in sorted(keep))}")
    if dropped:
        print(f"  fora (poucas cenas): {dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
