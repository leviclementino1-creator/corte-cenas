"""Em qual pasta este anime vai morar.

Antes: `output_path / sanitize(o que o usuário digitou)`. O nome vinha de
texto livre e o app não lembrava de nada — digitar "Mushoku Tensei" numa
análise e "Mushoku" na outra criava DUAS pastas do mesmo show, com o mesmo
episódio dos dois lados.

Agora a decisão é tomada uma vez e lembrada. Quando o nome digitado parece
com uma pasta que já existe, o app PERGUNTA (quem responde é a interface) e
guarda a resposta; da próxima vez nem pergunta.

A memória fica num `pastas.json` dentro do cache, e não numa coluna do banco,
porque a pasta precisa ser escolhida ANTES do registro do anime existir — o
pipeline corta as cenas primeiro e só depois resolve quem é o anime.

Por que perguntar em vez de adivinhar: no AniList cada TEMPORADA é um anime
diferente, com título diferente ("Mushoku Tensei III: Isekai Ittara Honki
Dasu" é a T3). Não há como o app saber sozinho que ela pertence à mesma pasta
da T1 — mas o usuário sabe, e responde uma vez só.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..naming import name_tokens
from .organizer import sanitize

ARQUIVO = "pastas.json"

# Quem pergunta é a interface: recebe (digitado, [pastas candidatas]) e
# devolve a pasta escolhida, ou None pra "nenhuma delas, cria a minha".
Perguntador = Callable[[str, list[str]], "str | None"]


def _chave(nome: str) -> str:
    """Duas escritas do mesmo nome caem na mesma chave. Reusa o mesmo
    tratamento de tokens do casamento de personagens — "Mushoku Tensei" e
    "mushoku  tensei" viram a mesma coisa."""
    return " ".join(sorted(name_tokens(nome)))


def _memoria(cache_dir: Path) -> dict[str, str]:
    p = Path(cache_dir) / ARQUIVO
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _grava(cache_dir: Path, mapa: dict[str, str]) -> None:
    p = Path(cache_dir) / ARQUIVO
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)   # troca atômica: nunca deixa um json pela metade


def parecidas(digitado: str, saida: Path) -> list[str]:
    """Pastas que já existem e podem ser a mesma série.

    Vale quando um nome contém o outro em palavras — "Mushoku Tensei" casa
    com a pasta "Mushoku". Não casa por prefixo solto ("Mush" não conta),
    porque isso juntaria shows que só começam igual.
    """
    saida = Path(saida)
    if not saida.exists():
        return []
    meus = name_tokens(digitado)
    if not meus:
        return []
    fora = []
    for d in sorted(saida.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        dela = name_tokens(d.name)
        if not dela:
            continue
        if dela == meus or dela < meus or meus < dela:
            fora.append(d.name)
    return fora


def resolver(
    digitado: str,
    saida: Path,
    cache_dir: Path,
    perguntar: Perguntador | None = None,
) -> Path:
    """A pasta do anime. Cria nada — só decide o caminho.

    Sem `perguntar`, o comportamento é o de sempre (usa o nome digitado):
    análise em lote e teste automático não podem travar esperando resposta.
    """
    saida = Path(saida)
    chave = _chave(digitado)
    mapa = _memoria(cache_dir)

    # 1. Já foi decidido antes pra este nome — nem pergunta.
    lembrada = mapa.get(chave)
    if lembrada:
        return saida / lembrada

    # 2. A pasta com o nome digitado já existe: é ela mesma, sem pergunta.
    natural = sanitize(digitado)
    if (saida / natural).exists():
        mapa[chave] = natural
        _grava(cache_dir, mapa)
        return saida / natural

    # 3. Existe pasta parecida? Quem decide é o usuário.
    candidatas = [c for c in parecidas(digitado, saida) if c != natural]
    if candidatas and perguntar is not None:
        escolhida = perguntar(digitado, candidatas)
        if escolhida:
            mapa[chave] = escolhida
            _grava(cache_dir, mapa)
            return saida / escolhida

    # 4. Nada parecido (ou o usuário quis a dele): o nome digitado.
    mapa[chave] = natural
    _grava(cache_dir, mapa)
    return saida / natural


def apontar(digitado: str, pasta: str, cache_dir: Path) -> None:
    """Grava a decisão na mão — pra quando o usuário arruma as pastas fora
    do app e quer que a próxima análise siga o novo arranjo."""
    mapa = _memoria(cache_dir)
    mapa[_chave(digitado)] = pasta
    _grava(cache_dir, mapa)
