"""Operações de curadoria compartilhadas entre a aba Resultados e a
conferência do elenco — sem Qt, testáveis."""
from __future__ import annotations

from pathlib import Path

from .storage.db import Database
from .storage.organizer import refresh_shot_links, sanitize


def apply_folder_moves(
    db: Database,
    episode_id: int,
    anime_id: int,
    episode_root: Path,
) -> dict[str, int]:
    """Explorer → app: clipe ARRASTADO pra pasta de outro personagem vira
    atribuição manual lembrada.

    O sincronizador só entendia exclusão: arrastar um clipe pra pasta certa
    era trabalho jogado fora, porque a análise seguinte reconstrói as pastas
    a partir do banco. Agora, clipe que aparece numa pasta onde o banco não
    tem aquele personagem é lido como "eu, humano, digo que ele está aqui" —
    entra no banco com confiança 1.0 e vira um `add` na memória de curadoria,
    que sobrevive a reanálise.

    Só ADIÇÃO: o que sumiu continua sendo tratado pelo caminho de remoção
    (que também registra bloqueio). Retorna {personagem: nº de cenas}.
    """
    by_char = Path(episode_root) / "by_character"
    if not by_char.exists():
        return {}
    shots = db.shots_for_episode(episode_id)
    id_by_file = {Path(s["file"]).name: s["id"] for s in shots}
    idx_by_id = {s["id"]: s["idx"] for s in shots}
    by_shot = db.assignments_for_episode(episode_id)

    known = {c["name"]: c["id"] for c in db.get_characters_for_anime(anime_id)}
    # pasta sanitizada -> nome real (os nomes têm vírgula, a pasta não)
    by_folder = {sanitize(n): n for n in known}

    added: dict[str, int] = {}
    for d in sorted(by_char.iterdir()):
        if not d.is_dir():
            continue
        name = by_folder.get(d.name)
        if name is None:
            # Pasta com nome que o banco não conhece: cria o personagem
            # (é o usuário batizando pelo Explorer).
            name = d.name
            known[name] = db.upsert_character(anime_id, name, anilist_id=None)
            by_folder[d.name] = name
        cid = known[name]
        for f in d.iterdir():
            if not f.is_file() or f.suffix.lower() != ".mp4":
                continue
            sid = id_by_file.get(f.name)
            if sid is None:
                continue
            if any(a["id"] == cid for a in by_shot.get(sid, [])):
                continue  # já está no banco
            # `_manual`: entra aprovada, o que a protege do descarte por
            # "poucas cenas" — decisão humana não é ruído estatístico.
            db.assign_character_manual(sid, cid, 1.0)
            db.record_manual(episode_id, int(idx_by_id[sid]), cid, "add", 1.0)
            added[name] = added.get(name, 0) + 1
    return added


def merge_shots(
    db: Database,
    episode_id: int,
    shot_rows: list[dict],
    episode_root: Path,
    keyframes_per_shot: int = 3,
    by_character: bool = True,
    by_pair: bool = True,
) -> dict | None:
    """Junta cenas VIZINHAS num clipe só, na hora.

    Existe porque o detector às vezes parte uma cena em duas — um flash, uma
    tremida de câmera, um fade — e o usuário quer o pedaço inteiro num
    arquivo. A junção é registrada por TEMPO no banco, então continua valendo
    nas próximas análises (é a mesma ideia da memória de curadoria).

    O clipe novo sai de CONCATENAR os que já existem, sem re-encodar: eles
    vieram do mesmo corte, com os mesmos parâmetros, e são contíguos — dá
    exatamente o mesmo vídeo em segundos, em vez de reprocessar do MKV.

    A cena resultante herda o número da primeira (ninguém é renumerado) e
    fica com a UNIÃO dos personagens. Devolve o registro da cena nova, ou
    None se não deu (não são vizinhas, arquivo faltando).
    """
    import subprocess
    import sys

    from .ffmpeg_locate import ffmpeg_binary
    from .keyframe_extractor import extract_keyframes
    from .shot_detection import ShotBounds

    if len(shot_rows) < 2:
        return None
    rows = sorted(shot_rows, key=lambda r: float(r["start"]))
    root = Path(episode_root)
    files = [root / r["file"] for r in rows]
    if not all(f.exists() and f.stat().st_size > 0 for f in files):
        return None
    # Vizinhas de verdade: o fim de uma encosta no começo da outra (folga de
    # meio segundo cobre o meio-frame de margem do corte).
    for a, b in zip(rows, rows[1:]):
        if float(b["start"]) - float(a["end"]) > 0.5:
            return None

    alvo = rows[0]
    novo_fim = max(float(r["end"]) for r in rows)
    destino = root / alvo["file"]

    lista = root / "shots" / "_concat.txt"
    lista.write_text(
        "".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8"
    )
    saida = destino.with_suffix(".merged.mp4")
    try:
        proc = subprocess.run(
            [
                str(ffmpeg_binary()), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(lista),
                "-c", "copy", str(saida),
            ],
            capture_output=True,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
        )
    finally:
        lista.unlink(missing_ok=True)
    if proc.returncode != 0 or not saida.exists() or saida.stat().st_size == 0:
        saida.unlink(missing_ok=True)
        print(f"[CorteCenas] Não consegui juntar as cenas: "
              f"{(proc.stderr or b'').decode('utf-8', 'replace')[:200]}")
        return None

    # Banco: a primeira absorve o intervalo e a união dos personagens; as
    # outras saem (com os clipes e espelhos delas).
    by_shot = db.assignments_for_episode(episode_id)
    uniao: dict[int, float] = {}
    for r in rows:
        for a in by_shot.get(int(r["id"]), []):
            uniao[int(a["id"])] = max(uniao.get(int(a["id"]), 0.0), float(a["confidence"] or 0))

    for r in rows[1:]:
        for a in by_shot.get(int(r["id"]), []):
            db.remove_shot_character(int(r["id"]), int(a["id"]))
        try:
            refresh_shot_links(root, root / r["file"], [],
                               by_character=by_character, by_pair=by_pair)
        except Exception:
            pass
        (root / r["file"]).unlink(missing_ok=True)
        for k in range(keyframes_per_shot):
            (root / "keyframes" / f"{int(r['idx']):04d}_{k}.jpg").unlink(missing_ok=True)
        db.delete_shot(int(r["id"]))

    destino.unlink(missing_ok=True)
    saida.replace(destino)
    db.set_shot_bounds(int(alvo["id"]), float(alvo["start"]), novo_fim)
    for cid, conf in uniao.items():
        db.assign_character(int(alvo["id"]), cid, conf)
    db.add_shot_merge(episode_id, float(alvo["start"]), novo_fim)

    extract_keyframes(
        destino,
        ShotBounds(idx=int(alvo["idx"]), start=0.0, end=novo_fim - float(alvo["start"])),
        root / "keyframes",
        n_frames=keyframes_per_shot,
        clip_path=destino,
    )
    nomes = [a["name"] for a in db.assignments_for_episode(episode_id).get(int(alvo["id"]), [])]
    try:
        refresh_shot_links(root, destino, nomes,
                           by_character=by_character, by_pair=by_pair)
    except Exception:
        pass
    return {"id": int(alvo["id"]), "idx": int(alvo["idx"]),
            "start": float(alvo["start"]), "end": novo_fim, "n": len(rows)}


def remove_character_from_episode(
    db: Database,
    episode_id: int,
    character_id: int,
    episode_root: Path,
    *,
    by_character: bool = True,
    by_pair: bool = True,
) -> int:
    """Remove o personagem do episódio INTEIRO: cada cena sai no banco,
    vira bloqueio lembrado (reanálise não devolve) e os hardlinks reais
    são sincronizados na hora — a pasta by_character dele esvazia e some.
    Clipes em shots/ e os outros personagens ficam. Retorna nº de cenas."""
    shots = db.shots_for_character(character_id, episode_id)
    if not shots:
        return 0
    for s in shots:
        db.remove_shot_character(int(s["id"]), character_id)
        db.record_manual(episode_id, int(s["idx"]), character_id, "block")
    try:
        root = Path(episode_root)
        by_shot = db.assignments_for_episode(episode_id)
        for s in shots:
            names_now = [a["name"] for a in by_shot.get(int(s["id"]), [])]
            refresh_shot_links(
                root, root / s["file"], names_now,
                by_character=by_character, by_pair=by_pair,
            )
    except Exception as e:
        print(f"[CorteCenas] Sincronização das pastas falhou: {e}")
    return len(shots)
