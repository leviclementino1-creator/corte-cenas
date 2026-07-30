"""Readotar episódios que estão no disco mas sumiram do banco.

Acontece de verdade: o usuário reorganiza as pastas no Explorer, apaga um
episódio do acervo sem levar a pasta junto, ou um banco velho é substituído.
O resultado é uma pasta com centenas de clipes que o app não enxerga — e uma
Biblioteca que mente sobre o acervo tanto quanto quando lista o que não
existe.

Dá pra reconstruir sem reanalisar nada porque toda análise deixa
`metadata/shots.json` com o mapa completo: arquivo, keyframe, começo, fim e
quem aparece em cada cena. Cortar de novo levaria dezenas de minutos e
gastaria GPU; ler o json leva milissegundos.

O que NÃO se inventa: personagem que o banco não conhece não é criado aqui.
Batizar personagem é decisão do usuário, e o dono dela é a tela de batismo.
A cena volta sem dono e aparece como "sem identificação" — honesto.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..naming import find_token_match

SLUG = re.compile(r"^S(\d{2})E(\d{2})$", re.I)


def _slug(nome: str) -> tuple[int, int] | None:
    m = SLUG.match(nome)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _episodios_no_banco(db) -> set[tuple[str, int, int]]:
    """(título minúsculo, temporada, episódio) do que o banco já conhece."""
    with db.connect() as c:
        return {
            (r["title"].lower(), int(r["season"]), int(r["episode"]))
            for r in c.execute(
                "SELECT a.title, e.season, e.episode FROM episode e "
                "JOIN anime a ON a.id = e.anime_id"
            )
        }


def orfas(saida: Path, db) -> list[dict]:
    """Pastas de episódio com clipes que o banco não conhece.

    Casa por (título do shots.json, temporada, episódio) e não pelo nome da
    pasta — a pasta pode ter sido renomeada, o json guarda o título de quando
    foi analisado.
    """
    saida = Path(saida)
    if not saida.exists():
        return []
    conhecidos = _episodios_no_banco(db)
    fora: list[dict] = []

    for pasta_anime in sorted(saida.iterdir()):
        if not pasta_anime.is_dir() or pasta_anime.name.startswith("_"):
            continue
        for pasta in sorted(pasta_anime.iterdir()):
            if not pasta.is_dir():
                continue
            se = _slug(pasta.name)
            if se is None:
                continue
            clipes = list((pasta / "shots").glob("*.mp4"))
            if not clipes:
                continue
            mapa = pasta / "metadata" / "shots.json"
            titulo = pasta_anime.name
            if mapa.exists():
                try:
                    d = json.loads(mapa.read_text(encoding="utf-8"))
                    if d and d[0].get("anime"):
                        titulo = d[0]["anime"]
                except (OSError, ValueError, IndexError, KeyError):
                    pass
            if (titulo.lower(), se[0], se[1]) in conhecidos:
                continue
            fora.append({
                "pasta": str(pasta),
                "anime": titulo,
                "temporada": se[0],
                "episodio": se[1],
                "clipes": len(clipes),
                "tem_mapa": mapa.exists(),
            })
    return fora


def readotar(db, pasta: Path) -> dict:
    """Recria no banco o episódio desta pasta. Nenhum arquivo é tocado."""
    pasta = Path(pasta)
    mapa = pasta / "metadata" / "shots.json"
    if not mapa.exists():
        return {"ok": False, "msg": "sem metadata/shots.json — não dá pra readotar"}

    try:
        cenas = json.loads(mapa.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "msg": f"shots.json ilegível: {e}"}
    if not cenas:
        return {"ok": False, "msg": "shots.json vazio"}

    se = _slug(pasta.name) or (
        int(cenas[0].get("season", 0)), int(cenas[0].get("episode", 0))
    )
    titulo = cenas[0].get("anime") or pasta.parent.name

    anime_id = db.upsert_anime(anilist_id=None, title=titulo)
    episode_id = db.upsert_episode(anime_id, se[0], se[1], str(pasta))
    db.clear_episode_shots(episode_id)

    # o elenco que o banco JÁ conhece deste anime; nada é criado aqui
    elenco = {c["name"]: int(c["id"]) for c in db.get_characters_for_anime(anime_id)}

    postas, ligadas, sem_dono, faltando = 0, 0, 0, set()
    for cena in cenas:
        arq = str(cena.get("file") or "")
        if not arq:
            continue
        # o json grava com barra normal; o banco guarda como o Windows usa
        arq_rel = arq.replace("/", "\\")
        if not (pasta / arq).exists() and not (pasta / arq_rel).exists():
            continue          # clipe que sumiu: não entra como cena fantasma
        kf = cena.get("keyframe")
        shot_id = db.insert_shot(
            episode_id=episode_id,
            idx=int(cena.get("shot_id", postas)),
            file=arq_rel,
            keyframe=str(kf).replace("/", "\\") if kf else None,
            start=float(cena.get("start", 0.0)),
            end=float(cena.get("end", 0.0)),
        )
        postas += 1

        quem = cena.get("characters") or []
        if not quem:
            sem_dono += 1
        for c in quem:
            nome = c.get("name") if isinstance(c, dict) else str(c)
            conf = float(c.get("confidence", 1.0)) if isinstance(c, dict) else 1.0
            cid = elenco.get(nome)
            if cid is None:
                # mesmo casamento por tokens do resto do app ("Tempest,
                # Rimuru" e "Rimuru Tempest" são a mesma pessoa)
                achado = find_token_match(nome, list(elenco))
                cid = elenco.get(achado) if achado else None
            if cid is None:
                faltando.add(nome)
                continue
            db.assign_character(shot_id, cid, conf)
            ligadas += 1

    msg = f"{postas} cenas readotadas · {ligadas} atribuições · {sem_dono} sem dono"
    if faltando:
        msg += (f" · {len(faltando)} personagem(ns) que o banco não conhece "
                f"ficaram de fora: {', '.join(sorted(faltando)[:3])}"
                + ("…" if len(faltando) > 3 else ""))
    return {
        "ok": True, "msg": msg, "episode_id": episode_id,
        "cenas": postas, "atribuicoes": ligadas, "faltando": sorted(faltando),
    }
