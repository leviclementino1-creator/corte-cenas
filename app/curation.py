"""Operações de curadoria compartilhadas entre a aba Resultados e a
conferência do elenco — sem Qt, testáveis."""
from __future__ import annotations

from pathlib import Path

from .storage.db import Database
from .storage.organizer import refresh_shot_links


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
