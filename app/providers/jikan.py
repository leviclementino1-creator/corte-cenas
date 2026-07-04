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


class JikanClient:
    """Thin wrapper around Jikan v4. Rate-limit respectful (~3 req/s)."""

    def __init__(self, timeout: float = 30.0, min_interval: float = 0.4) -> None:
        self.client = httpx.Client(timeout=timeout, headers={"Accept": "application/json"})
        self.min_interval = min_interval
        self._last = 0.0

    def close(self) -> None:
        self.client.close()

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()

    def _get(self, path: str) -> dict | None:
        for attempt in range(3):
            self._throttle()
            r = self.client.get(f"{JIKAN_BASE}{path}")
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(1.0 + attempt)
                continue
            return None
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
