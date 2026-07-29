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
