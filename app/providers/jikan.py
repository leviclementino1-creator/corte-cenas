from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

JIKAN_BASE = "https://api.jikan.moe/v4"


@dataclass
class JikanCharacter:
    mal_id: int
    name: str
    role: str
    image: str | None


@dataclass
class JikanAnime:
    mal_id: int
    title: str
    title_english: str | None
    cover: str | None


class JikanClient:
    """Thin wrapper around Jikan v4. Rate-limit respectful (~3 req/s)."""

    def __init__(self, timeout: float = 30.0, min_interval: float = 0.4) -> None:
        # Accept-Encoding: gzip EXATO — descoberta de produção (jul/2026): o
        # cache nginx do api.jikan.moe guarda variantes por Accept-Encoding.
        # Com "gzip" a resposta vem do cache (até STALE, servida mesmo com o
        # backend morto -> 200); com a lista padrão do httpx ("gzip, deflate,
        # br, zstd") o pedido FURA o cache e cai no backend saturado -> 504.
        # Era por isso que o app "sempre falhava" enquanto o site funcionava.
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Accept": "application/json", "Accept-Encoding": "gzip"},
        )
        self.min_interval = min_interval
        self._last = 0.0
        # Chamadas que morreram mesmo após retries — o provider compara
        # antes/depois pra saber se o MyAnimeList estava fora do ar (e avisar
        # o usuário em vez de entregar um "0 personagens" mudo).
        self.failures = 0
        # DISJUNTOR: com o Jikan em crise (dias inteiros de 504), insistir
        # em 80 galerias × 3 retries × timeout = minutos de espera pra
        # nada, TODA análise. Depois de N falhas seguidas o cliente se
        # declara morto pra ESTA execução: as chamadas seguintes voltam
        # None na hora e as reservas (AniList/Kitsu) assumem imediatamente.
        # Um sucesso zera a contagem — instabilidade pontual não desarma.
        self._consecutive_failures = 0
        self.dead = False
        # Disjuntor POR FAMÍLIA de endpoint. Medido em jul/2026: o
        # /characters/<id>/pictures devolve 504 em 100% das tentativas
        # enquanto o /anime/<id>/characters responde 200 em 0.25s no MESMO
        # segundo — o endpoint de galeria está morto, o de elenco não. Com
        # um disjuntor único, a galeria derrubava o elenco junto.
        self._dead_families: set[str] = set()
        self._family_failures: dict[str, int] = {}

    def close(self) -> None:
        self.client.close()

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()

    _BREAKER_TRIP = 2      # falhas SEGUIDAS que desarmam o disjuntor
    _FAMILY_TRIP = 2       # ...só nesta família de endpoint
    _RETRIES = 2

    @staticmethod
    def _family(path: str) -> str:
        """'/characters/123/pictures' -> 'characters/pictures'. Agrupa
        chamadas do mesmo tipo, sem o id no meio."""
        parts = [p for p in path.split("?")[0].split("/") if p and not p.isdigit()]
        return "/".join(parts[:2])

    def _get(self, path: str) -> dict | None:
        family = self._family(path)
        if self.dead or family in self._dead_families:
            self.failures += 1
            return None
        last_status: int | str = "?"
        # Escada de retry curta: 0.5s, 1.0s. A antiga (1+2+3 = 6s por
        # chamada morta, 3 chamadas pra armar) gastava 20s medidos só pra
        # concluir o que a primeira resposta já dizia.
        for attempt in range(self._RETRIES):
            self._throttle()
            try:
                r = self.client.get(f"{JIKAN_BASE}{path}")
            except httpx.HTTPError as e:
                last_status = type(e).__name__
                time.sleep(0.5 * (2 ** attempt))
                continue
            if r.status_code == 200:
                self._consecutive_failures = 0
                return r.json()
            last_status = r.status_code
            # Jikan runs on shared infra and throws transient 429/5xx under
            # load (July 2026: whole days of 504s). Retry both — giving up on
            # the first 504 was silently gutting the reference bank.
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(0.5 * (2 ** attempt))
                continue
            break
        print(f"[Jikan] {path} falhou apos retries (HTTP {last_status})", flush=True)
        self.failures += 1
        self._consecutive_failures += 1
        self._family_failures[family] = self._family_failures.get(family, 0) + 1
        if self._family_failures[family] >= self._FAMILY_TRIP:
            if family not in self._dead_families:
                self._dead_families.add(family)
                print(
                    f"[Jikan] endpoint '{family}' fora do ar — pulando ele "
                    "NESTA análise (os outros continuam funcionando).",
                    flush=True,
                )
        # Disjuntor GERAL: só quando DUAS famílias diferentes caem é que o
        # MyAnimeList está realmente fora do ar. Antes, o /pictures morto
        # (que vive fora do ar) matava junto o /anime/characters que estava
        # respondendo — e o elenco inteiro caía pras reservas à toa.
        if len(self._dead_families) >= 2 and not self.dead:
            self.dead = True
            print(
                "[Jikan] duas famílias de endpoint fora do ar — desistindo do "
                "MyAnimeList NESTA análise (as reservas AniList/Kitsu assumem "
                "na hora, sem esperar 80 timeouts).",
                flush=True,
            )
        return None

    def search_anime(self, name: str) -> JikanAnime | None:
        """Fallback for when the AniList API is down. Returns the first
        TV/movie hit, ranked by MAL popularity — which is a strong enough
        signal for well-known series."""
        # sfw=true drops H-tag results; type=... keeps it to actual anime.
        # We URL-encode `name` manually since httpx is passing it via path.
        from urllib.parse import quote
        path = f"/anime?q={quote(name)}&limit=5&sfw=true&order_by=popularity&sort=asc"
        data = self._get(path)
        if not data:
            return None
        for entry in data.get("data") or []:
            mal_id = entry.get("mal_id")
            if mal_id is None:
                continue
            # Prefer TV / movie / ONA / OVA; skip music videos, promos, etc.
            type_ = (entry.get("type") or "").lower()
            if type_ and type_ in ("music", "cm", "pv"):
                continue
            title_en = entry.get("title_english")
            title = entry.get("title") or title_en or f"MAL {mal_id}"
            cover = (entry.get("images") or {}).get("jpg", {}).get("large_image_url")
            return JikanAnime(
                mal_id=mal_id, title=title, title_english=title_en, cover=cover
            )
        return None

    def anime_characters(self, mal_id: int) -> list[JikanCharacter]:
        data = self._get(f"/anime/{mal_id}/characters")
        if not data:
            return []
        out: list[JikanCharacter] = []
        for entry in data.get("data") or []:
            ch = entry.get("character") or {}
            cid = ch.get("mal_id")
            if cid is None:
                continue
            name = ch.get("name") or f"Character {cid}"
            img = (ch.get("images") or {}).get("jpg", {}).get("image_url")
            role = entry.get("role") or "Supporting"
            out.append(JikanCharacter(mal_id=cid, name=name, role=role, image=img))
        return out

    def character_pictures(self, mal_id: int) -> list[str]:
        data = self._get(f"/characters/{mal_id}/pictures")
        if not data:
            return []
        urls: list[str] = []
        for entry in data.get("data") or []:
            img = entry.get("jpg") or entry.get("webp") or {}
            url = img.get("image_url") or img.get("large_image_url")
            if url:
                urls.append(url)
        return urls
