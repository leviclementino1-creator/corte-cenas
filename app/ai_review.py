"""AI-assisted review of ambiguous shot classifications.

For shots where the CLIP pipeline has low-to-mid confidence, we send the
keyframe plus a handful of candidate character references to a vision-LLM
(Gemini via NavyAI, or any OpenAI-compatible gateway) and ask it to pick
the best match. The result can override/confirm the CLIP assignment.

This module is intentionally self-contained — no Qt, no app state — so it
can be called from a worker thread or tested in isolation.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx


class NavyAIClient:
    """Minimal OpenAI-compatible client for NavyAI / any gateway that
    speaks the same schema. Supports image_url inline data URLs.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.navy/v1",
        model: str = "gemini-2.0-flash",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _data_url(img_bytes: bytes, mime: str = "image/jpeg") -> str:
        return f"data:{mime};base64,{base64.b64encode(img_bytes).decode('ascii')}"

    def _build_payload(
        self, content: list[dict], max_tokens: int = 300
    ) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

    def _extract_content_and_usage(
        self, data: dict
    ) -> tuple[str | None, dict]:
        """Unpack /chat/completions response into (raw_content, usage_dict)."""
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        try:
            raw_content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raw_content = None
        return raw_content, usage

    def _post_with_retry(self, payload: dict, retries: int = 2) -> dict:
        """Post to /chat/completions with simple retry for 5xx errors."""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=payload)
                if r.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"NavyAI API error after {retries + 1} attempts: {last_err}")

    @staticmethod
    def _parse_json_response(content: str) -> dict | None:
        """Parse the model's content string into a dict, being forgiving:
          - strips ``` fences
          - unwraps single-element arrays
          - handles truncated JSON (returns None)
        """
        if not content:
            return None
        s = content.strip()
        if s.startswith("```"):
            s = s.strip("`").split("\n", 1)[1] if "\n" in s else s
            s = s.rstrip("`").strip()
            if s.lower().startswith("json"):
                s = s[4:].strip()
        try:
            parsed = json.loads(s)
        except Exception:
            return None
        if isinstance(parsed, list):
            if not parsed:
                return None
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            return None
        return parsed

def classify_frame(
    client: NavyAIClient,
    frame_bytes: bytes,
    character_names: list[str],
    anime_title: str,
    top_refs: dict[str, list[bytes]] | None = None,
) -> tuple[str | None, float, str | None, dict]:
    """Ask the LLM to pick one character (or 'none') from a known roster,
    given a single query frame and optional visual references for the most
    common characters.
    """
    if not character_names:
        return None, 0.0, None, {}

    preamble = [
        f'You are identifying characters from the anime "{anime_title}".',
        "",
        "Look at the query frame. Return \"none\" in ALL of these cases:",
        "• The frame shows only scenery, objects, text, UI, a hand, a weapon, food, etc.",
        "• The character's face is not visible (back view, behind object, too dark).",
        "• You can see a face but it does not clearly match any listed character.",
        "• Two characters are plausible and you can't pick one with certainty.",
        "• The character you're thinking of is not in the list below.",
        "",
        "Only pick a character if the face/hair/outfit visibly matches that specific "
        "character. Do NOT guess from props, colors, or context. When in doubt, say \"none\".",
        "",
        "Return ONLY JSON: {\"character\": \"<exact name from list or 'none'>\", "
        "\"confidence\": <0-1>, \"reason\": \"<specific visual feature>\"}.",
        "",
        "Known characters: " + ", ".join(character_names),
    ]

    content: list[dict] = [{"type": "text", "text": "\n".join(preamble)}]
    content.append({"type": "text", "text": "QUERY FRAME:"})
    content.append({"type": "image_url", "image_url": {"url": client._data_url(frame_bytes)}})

    if top_refs:
        for name, refs in top_refs.items():
            if not refs:
                continue
            content.append({"type": "text", "text": f"REFERENCE — {name}:"})
            for ref in refs[:1]:  # one ref per char to keep prompt tight
                content.append({"type": "image_url", "image_url": {"url": client._data_url(ref)}})

    data = client._post_with_retry(client._build_payload(content))
    raw_content, usage = client._extract_content_and_usage(data)
    if raw_content is None:
        return None, 0.0, None, usage

    parsed = client._parse_json_response(raw_content)
    if parsed is None:
        return None, 0.0, (raw_content or "")[:120], usage

    name = (parsed.get("character") or "").strip() if isinstance(parsed.get("character"), str) else ""
    try:
        conf = float(parsed.get("confidence") or 0.0)
    except (ValueError, TypeError):
        conf = 0.0
    reason = parsed.get("reason") if isinstance(parsed.get("reason"), str) else None
    if not name or name.lower() == "none":
        return None, conf, reason, usage
    return name, conf, reason, usage


def classify_face_crops(
    client: NavyAIClient,
    face_crops_bytes: list[bytes],
    character_names: list[str],
    anime_title: str,
    top_refs: dict[str, list[bytes]] | None = None,
) -> tuple[list[tuple[str, float]], dict]:
    """Hybrid mode: you already have face crops from YOLO. Send them to the
    model and ask 'for each face, which character is it?'. Returns a list of
    (name, confidence) — one per face — plus the usage dict.

    Sending tight face crops (vs the whole keyframe) is cheaper and more
    accurate: the model isn't distracted by scenery/other chars.
    """
    if not face_crops_bytes or not character_names:
        return [], {}

    preamble = [
        f'You are identifying anime characters from "{anime_title}".',
        "Each image below is a face crop detected in a single shot.",
        "For EACH face, identify which listed character it matches.",
        "",
        "DECISIVE FEATURES to look for (in order of importance):",
        "1. Hair color + hairstyle (most distinctive in anime).",
        "2. Accessories attached to the head (headband, hat, bow, goggles).",
        "3. Eye color + shape.",
        "4. Outfit/collar if visible.",
        "Similar-looking male protagonists with different hair colors are DIFFERENT characters — do not merge them.",
        "",
        "Return \"none\" for a face if ANY of these apply:",
        "• The face does not clearly match any listed character's distinctive hair/accessories.",
        "• Two characters could plausibly fit and you can't pick one.",
        "• The crop is too blurry, dark, or occluded to identify.",
        "• The character is unlisted.",
        "",
        "Do not force a match. It is better to return \"none\" than to guess.",
        "",
        "Return ONLY JSON: {\"faces\": [{\"character\": \"<name or 'none'>\", "
        "\"confidence\": <0-1>, \"reason\": \"<specific visual feature>\"}, ...]}.",
        "The faces array must have exactly " + str(len(face_crops_bytes))
        + " entries, one per input face, in the same order.",
        "",
        "Known characters: " + ", ".join(character_names),
    ]

    content: list[dict] = [{"type": "text", "text": "\n".join(preamble)}]
    for i, face in enumerate(face_crops_bytes, 1):
        content.append({"type": "text", "text": f"FACE {i}:"})
        content.append({"type": "image_url", "image_url": {"url": client._data_url(face)}})

    if top_refs:
        content.append({"type": "text", "text": "Character references:"})
        for name, refs in top_refs.items():
            if not refs:
                continue
            content.append({"type": "text", "text": f"— {name}:"})
            for ref in refs[:1]:
                content.append({"type": "image_url", "image_url": {"url": client._data_url(ref)}})

    data = client._post_with_retry(client._build_payload(content, max_tokens=400))
    raw, usage = client._extract_content_and_usage(data)
    if raw is None:
        return [], usage
    parsed = client._parse_json_response(raw)
    if not parsed:
        return [], usage

    faces = parsed.get("faces") if isinstance(parsed, dict) else None
    if not isinstance(faces, list):
        return [], usage

    out: list[tuple[str, float]] = []
    for entry in faces:
        if not isinstance(entry, dict):
            out.append(("none", 0.0))
            continue
        name = entry.get("character")
        if not isinstance(name, str) or name.lower() == "none":
            name = "none"
        try:
            conf = float(entry.get("confidence") or 0.0)
        except (ValueError, TypeError):
            conf = 0.0
        out.append((name, conf))
    return out, usage
