from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .anilist import AniListAnime, AniListClient, AniListRelation
from .danbooru import DanbooruClient, character_tag_candidates
from .jikan import JikanClient

_ROLE_WEIGHT = {"Main": 0, "MAIN": 0, "Supporting": 1, "SUPPORTING": 1, "Background": 2, "BACKGROUND": 2}

# Relation types treated as "same franchise" — we pool characters/refs across
# these to give seasons like Dr. Stone S4 access to refs from S1-S3.
_FRANCHISE_RELATIONS = {
    "SEQUEL", "PREQUEL", "SIDE_STORY", "PARENT",
    "ALTERNATIVE", "SPIN_OFF", "SUMMARY",
}

# Bump when the cached metadata schema changes so old caches are rebuilt.
# v4: characters/refs pooled across the whole franchise graph.
METADATA_VERSION = 4


@dataclass
class CharacterRef:
    mal_id: int | None
    anilist_id: int | None
    name: str
    role: str
    image_urls: list[str]


@dataclass
class AnimeBundle:
    anilist_id: int | None
    mal_id: int | None
    title: str
    title_english: str | None
    characters: list[CharacterRef]
    franchise_ids: list[int] | None = None   # All AniList IDs pooled into this bundle
    franchise_root_id: int | None = None     # The id used as cache key for this franchise


class AnimeProvider:
    """Resolves an anime name into a character list with multiple reference URLs.

    Caches results under `<cache>/anime_db/<anilist_id_or_mal>/metadata.json`
    so re-running on another episode of the same anime reuses the bank.
    """

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = Path(cache_root) / "anime_db"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.anilist = AniListClient()
        self.jikan = JikanClient()
        self.danbooru = DanbooruClient()

    def close(self) -> None:
        self.anilist.close()
        self.jikan.close()
        self.danbooru.close()

    def _cache_id(self, anilist_id: int | None, mal_id: int | None) -> str:
        if anilist_id:
            return f"al{anilist_id}"
        if mal_id:
            return f"mal{mal_id}"
        return "unknown"

    def _collect_franchise(
        self,
        primary_anilist_id: int,
        on_status: Callable[[str], None] | None,
        max_nodes: int = 25,
    ) -> list[AniListRelation]:
        """Full BFS of the AniList relation graph, following franchise edges
        in both directions (sequels + prequels). Returns all related anime.
        Capped at `max_nodes` to protect against pathological graphs.
        """
        seen: set[int] = {primary_anilist_id}
        collected: list[AniListRelation] = []

        frontier: list[int] = [primary_anilist_id]
        while frontier and len(seen) < max_nodes:
            next_frontier: list[int] = []
            for node_id in frontier:
                try:
                    rels = self.anilist.get_relations(node_id)
                except Exception:
                    continue
                for r in rels:
                    if r.relation_type not in _FRANCHISE_RELATIONS:
                        continue
                    if r.anilist_id in seen:
                        continue
                    seen.add(r.anilist_id)
                    collected.append(r)
                    next_frontier.append(r.anilist_id)
                    if len(seen) >= max_nodes:
                        break
                if len(seen) >= max_nodes:
                    break
            frontier = next_frontier

        if on_status and collected:
            on_status(f"Franquia: {len(collected) + 1} temporadas/entradas relacionadas.")
        return collected

    def _meta_path(self, cache_id: str) -> Path:
        # Resolve '<title> [al<id>]' folder if it already exists, else new path
        suffix = f"[{cache_id}]"
        if self.cache_root.exists():
            for p in self.cache_root.iterdir():
                if p.is_dir() and p.name.endswith(suffix):
                    return p / "metadata.json"
        return self.cache_root / cache_id / "metadata.json"

    def load_cached(self, cache_id: str) -> AnimeBundle | None:
        p = self._meta_path(cache_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if int(data.get("version", 1)) < METADATA_VERSION:
                return None  # schema changed, force rebuild
            chars = [CharacterRef(**c) for c in data["characters"]]
            return AnimeBundle(
                anilist_id=data.get("anilist_id"),
                mal_id=data.get("mal_id"),
                title=data["title"],
                title_english=data.get("title_english"),
                characters=chars,
                franchise_ids=data.get("franchise_ids"),
                franchise_root_id=data.get("franchise_root_id"),
            )
        except Exception:
            return None

    def save_cache(self, cache_id: str, bundle: AnimeBundle) -> None:
        # Prefer the '<title> [al<id>]' folder name so users can browse
        # cache/anime_db/ and actually tell which anime is which.
        p = self._meta_path(cache_id)
        if not p.parent.exists():
            from ..storage.organizer import sanitize
            safe_title = sanitize(bundle.title or cache_id)
            p = self.cache_root / f"{safe_title} [{cache_id}]" / "metadata.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": METADATA_VERSION,
            "anilist_id": bundle.anilist_id,
            "mal_id": bundle.mal_id,
            "title": bundle.title,
            "title_english": bundle.title_english,
            "franchise_ids": bundle.franchise_ids,
            "franchise_root_id": bundle.franchise_root_id,
            "characters": [asdict(c) for c in bundle.characters],
        }
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def resolve(
        self,
        anime_name: str,
        max_characters: int,
        images_per_character: int,
        on_status: Callable[[str], None] | None = None,
        use_danbooru: bool = False,
        season: int = 1,
    ) -> AnimeBundle:
        def status(msg: str) -> None:
            if on_status:
                on_status(msg)

        # For season > 1 we have to disambiguate on AniList, because the
        # plain search picks the most popular entry (usually S1). AniList
        # understands "Season N" as a semantic hint.
        queries = [anime_name]
        if season and season > 1:
            queries = [f"{anime_name} Season {season}", anime_name]

        anime: AniListAnime | None = None
        used_query: str | None = None
        for q in queries:
            status(f"Buscando '{q}' na AniList...")
            anime = self.anilist.search_anime(q)
            if anime is not None:
                used_query = q
                break
        if anime is None:
            raise RuntimeError(
                f"Anime '{anime_name}' não foi encontrado na AniList. "
                "Verifique o nome (sem tags de fansub ou qualidade) e tente de novo."
            )
        if used_query and used_query != anime_name:
            status(f"Resolvido: '{used_query}' -> {anime.title}")
        anilist_id = anime.id
        mal_id = anime.mal_id
        title = anime.title
        title_en = anime.title_english

        cache_id = self._cache_id(anilist_id, mal_id)
        cached = self.load_cached(cache_id)
        if cached is not None and cached.characters:
            status(f"Reusando banco cacheado ({len(cached.characters)} personagens).")
            return cached

        if mal_id is None:
            raise RuntimeError(
                f"Não foi possível obter MAL id para '{anime_name}'. "
                "O banco de personagens depende de Jikan (MyAnimeList)."
            )

        # 1) Traverse the franchise graph so we pool characters from all
        # related seasons (Dr. Stone S4 alone has almost no refs on MAL;
        # S1/S2 have tons).
        status("Mapeando temporadas relacionadas...")
        relations = self._collect_franchise(anilist_id, on_status=status)
        franchise_mal_ids = [mal_id] + [r.mal_id for r in relations if r.mal_id]
        franchise_anilist_ids = sorted({anilist_id, *(r.anilist_id for r in relations)})
        # Use the smallest AniList id as the franchise root — for long-running
        # series that's usually the earliest aired / canonical root.
        root_id = min(franchise_anilist_ids) if franchise_anilist_ids else anilist_id

        # Override cache_id to use franchise root so all seasons share storage.
        cache_id = f"al{root_id}"
        cached = self.load_cached(cache_id)
        if cached is not None and cached.characters:
            status(f"Reusando banco cacheado ({len(cached.characters)} personagens).")
            return cached

        status(f"Baixando personagens de {len(franchise_mal_ids)} temporada(s)...")

        # 2) Collect characters from every season, merge by name.
        merged: dict[str, dict] = {}     # key = lowercase name
        for m_id in franchise_mal_ids:
            try:
                season_chars = self.jikan.anime_characters(m_id)
            except Exception:
                continue
            for jc in season_chars:
                key = jc.name.lower().strip()
                if key not in merged:
                    merged[key] = {
                        "mal_id": jc.mal_id,
                        "name": jc.name,
                        "role": jc.role,
                        "image": jc.image,
                        "source_mal_ids": {m_id},
                    }
                else:
                    merged[key]["source_mal_ids"].add(m_id)
                    # Prefer a "Main" role if any season says so.
                    if _ROLE_WEIGHT.get(jc.role, 3) < _ROLE_WEIGHT.get(merged[key]["role"], 3):
                        merged[key]["role"] = jc.role

        # Sort main first then supporting, cap at max_characters.
        sorted_chars = sorted(
            merged.values(), key=lambda c: (_ROLE_WEIGHT.get(c["role"], 3), c["name"])
        )[:max_characters]

        resolved: list[CharacterRef] = []
        for i, ch in enumerate(sorted_chars, 1):
            status(f"Coletando imagens ({i}/{len(sorted_chars)}): {ch['name']}")

            # 1. Jikan — pictures for this character's MAL id. A character has
            # one canonical MAL id even if they appear in many seasons, so one
            # call is enough.
            jikan_urls: list[str] = []
            pics = self.jikan.character_pictures(ch["mal_id"])
            for u in pics:
                if u not in jikan_urls:
                    jikan_urls.append(u)

            # 2. Danbooru (optional, collages can contaminate centroids).
            dbooru_urls: list[str] = []
            if use_danbooru:
                series_variants = [t for t in (title, title_en) if t]
                tag_candidates = character_tag_candidates(ch["name"], *series_variants)
                dbooru_urls, matched_tag = self.danbooru.fetch_image_urls(
                    tag_candidates, limit=images_per_character
                )
                if matched_tag:
                    status(f"  Danbooru ✓ ({ch['name']} -> {matched_tag}, {len(dbooru_urls)} imgs)")

            urls: list[str] = []
            for u in jikan_urls + dbooru_urls:
                if u not in urls:
                    urls.append(u)
            if not urls and ch.get("image"):
                urls.append(ch["image"])

            resolved.append(
                CharacterRef(
                    mal_id=ch["mal_id"],
                    anilist_id=None,
                    name=ch["name"],
                    role=ch["role"],
                    image_urls=urls,
                )
            )

        bundle = AnimeBundle(
            anilist_id=anilist_id,
            mal_id=mal_id,
            title=title,
            title_english=title_en,
            characters=resolved,
            franchise_ids=franchise_anilist_ids,
            franchise_root_id=root_id,
        )
        self.save_cache(cache_id, bundle)
        return bundle
