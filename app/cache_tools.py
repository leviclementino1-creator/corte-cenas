"""Faxina do cache — as operações por trás dos botões de limpeza das
Configurações. Sem Qt aqui: tudo testável e chamável de qualquer lugar.

O problema que isso resolve: as galerias online trazem lixo (fanart com
outro personagem, retrato de outra temporada) que envenena os protótipos —
e até hoje limpar exigia caçar as pastas na mão. As refs têm três origens
distinguíveis pelo NOME do arquivo:

  • catálogo (baixadas): nome = hash hex de 16 dígitos ("0805708d….jpg")
  • batismo (Descoberta):  "auto_disc_NN.jpg"
  • manuais (o usuário):   qualquer outro nome

Limpar "o que veio da internet" = apagar só os hashes — o trabalho manual
e os batismos ficam."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

# hash de 16 hex + extensão de imagem = download de catálogo
_CATALOG_RE = re.compile(r"[0-9a-f]{16}\.(jpg|jpeg|png|webp)$", re.IGNORECASE)

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def refs_root(cache_path: Path) -> Path:
    """Raiz das referências: um subdiretório por anime, cada um com
    characters/<Nome>/*.jpg."""
    return Path(cache_path) / "anime_db"


def clean_catalog_refs(cache_path: Path) -> tuple[int, int]:
    """Apaga SÓ as imagens baixadas do catálogo (nome-hash) em todos os
    animes, junto com as pastas _filtered e os caches de embedding de refs
    (que ficariam órfãos). Batismos (auto_disc_*) e arquivos manuais ficam.

    Retorna (arquivos_apagados, animes_afetados)."""
    root = refs_root(cache_path)
    if not root.exists():
        return 0, 0
    removed = 0
    animes = 0
    for anime_dir in root.iterdir():
        if not anime_dir.is_dir():
            continue
        touched = False
        chars_dir = anime_dir / "characters"
        if chars_dir.exists():
            for char_dir in chars_dir.iterdir():
                if not char_dir.is_dir():
                    continue
                filtered = char_dir / "_filtered"
                if filtered.exists():
                    shutil.rmtree(filtered, ignore_errors=True)
                    touched = True
                for f in char_dir.iterdir():
                    if f.is_file() and _CATALOG_RE.fullmatch(f.name):
                        try:
                            f.unlink()
                            removed += 1
                            touched = True
                        except OSError:
                            pass
        # cache de embeddings das refs: metade dos arquivos sumiu — recomeça
        npz = anime_dir / "ref_features.npz"
        if touched and npz.exists():
            try:
                npz.unlink()
            except OSError:
                pass
        if touched:
            animes += 1
    return removed, animes


def wipe_cache(cache_path: Path) -> None:
    """Apagão: TODO o conteúdo do cache — refs (inclusive batismos e
    manuais!), banco de resultados/curadoria (index.db) e caches de elenco.
    Modelos e a pasta Output não são tocados. As pastas base são recriadas."""
    root = Path(cache_path)
    if not root.exists():
        return
    for child in root.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def refs_summary(cache_path: Path) -> tuple[int, int, int]:
    """(catálogo, batismo, manuais) — contagem de imagens por origem, pra
    mostrar no diálogo antes de limpar."""
    root = refs_root(cache_path)
    catalog = disc = manual = 0
    if not root.exists():
        return 0, 0, 0
    for f in root.glob("*/characters/*/*"):
        if not f.is_file() or f.suffix.lower() not in _IMG_EXTS:
            continue
        if f.parent.name == "_filtered":
            continue
        if _CATALOG_RE.fullmatch(f.name):
            catalog += 1
        elif f.name.startswith("auto_disc_"):
            disc += 1
        else:
            manual += 1
    return catalog, disc, manual
