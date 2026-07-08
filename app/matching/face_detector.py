from __future__ import annotations

from pathlib import Path

import cv2
import httpx
import numpy as np


# --- Legacy lbpcascade_animeface download (kept as a last-resort fallback) ---

_LBP_URL = (
    "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml"
)
_LBP_FILENAME = "lbpcascade_animeface.xml"


def ensure_cascade(models_dir: Path) -> Path:
    """Download the lbpcascade_animeface XML (fallback detector)."""
    models_dir.mkdir(parents=True, exist_ok=True)
    p = models_dir / _LBP_FILENAME
    if p.exists() and p.stat().st_size > 0:
        return p
    with httpx.Client(timeout=30.0, follow_redirects=True) as c:
        r = c.get(_LBP_URL)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


# --- Primary detector: YOLOv8 trained on anime (deepghs) -------------------

_YOLO_REPO = "deepghs/anime_face_detection"
_YOLO_FILE = "face_detect_v1.4_s/model.pt"  # 22MB, ~34ms/img on GPU

# Cascade stage 2: HEAD detection. The face model is mostly frontal — it
# misses profiles, downcast faces and small/far characters (production logs:
# faces found in only ~40-55% of shots). Heads are visible from any angle,
# so on face-miss frames we fall back to head boxes; the head crop (face +
# hair) is actually a strong input for CLIP identification.
_HEAD_REPO = "deepghs/anime_head_detection"
_HEAD_FILE = "head_detect_v0.5_s/model.pt"


def ensure_yolo_anime_face() -> Path:
    """Download (and cache) the deepghs anime face YOLOv8 model from HuggingFace.
    Raises RuntimeError with a helpful message if huggingface_hub is missing.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:
        raise RuntimeError(
            "huggingface_hub não está instalado. Rode `pip install huggingface_hub`."
        ) from e
    return Path(hf_hub_download(repo_id=_YOLO_REPO, filename=_YOLO_FILE))


def ensure_yolo_anime_head() -> Path:
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo_id=_HEAD_REPO, filename=_HEAD_FILE))


class AnimeFaceDetector:
    """Anime-face detection. Uses a YOLOv8 model trained on anime faces
    (deepghs/anime_face_detection) when `ultralytics` is available, and
    falls back to nagadomi's lbpcascade_animeface XML if not.

    Same output shape as the old cascade-based detector so callers don't
    need to change: `detect(img)` -> list[(x, y, w, h)].
    """

    def __init__(
        self,
        cascade_path: Path | None = None,
        conf: float = 0.4,
        use_cuda: bool = True,
    ) -> None:
        self.conf = conf
        self._yolo = None
        self._head_yolo = None
        self._cascade = None

        try:
            from ultralytics import YOLO
            model_path = ensure_yolo_anime_face()
            self._yolo = YOLO(str(model_path))
            self._device = "cuda" if use_cuda else "cpu"
        except Exception as e:
            # Graceful fallback to lbpcascade if YOLO unavailable.
            print(f"[CorteCenas] YOLO anime-face indisponível ({e}); usando lbpcascade.")
            path = cascade_path
            if path is None:
                path = ensure_cascade(Path("models"))
            self._cascade = cv2.CascadeClassifier(str(path))
            if self._cascade.empty():
                raise RuntimeError(f"Failed to load cascade: {path}") from e

        # Stage 2 (best-effort): head detector for face-miss frames. If the
        # download/load fails, the cascade quietly degrades to face-only.
        if self._yolo is not None:
            try:
                from ultralytics import YOLO
                self._head_yolo = YOLO(str(ensure_yolo_anime_head()))
            except Exception as e:
                print(f"[CorteCenas] Head-detect indisponível ({e}); só face detect.")
                self._head_yolo = None

    def detect(
        self,
        image_bgr: np.ndarray,
        min_size: int = 32,
        max_ratio: float = 0.75,
    ) -> list[tuple[int, int, int, int]]:
        """Detect anime faces.

        Filters out:
          • tiny faces (h < `min_size`) — usually scenery FPs
          • extreme close-ups (w/W or h/H >= `max_ratio`) — the face covers
            most of the frame, meaning we only see partial features
            (mouth + eye, or a cheek), which downstream identifiers cannot
            reliably match to a specific character.
        """
        if image_bgr is None or image_bgr.size == 0:
            return []
        img_h, img_w = image_bgr.shape[:2]

        def _size_filter(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
            kept = []
            for (x, y, w, h) in boxes:
                if h < min_size:
                    continue
                if max_ratio > 0 and (w / img_w >= max_ratio or h / img_h >= max_ratio):
                    continue
                kept.append((x, y, w, h))
            return kept

        if self._yolo is not None:
            out = _size_filter(self._predict_boxes(self._yolo, image_bgr))
            if out or self._head_yolo is None:
                return out
            # Cascade stage 2: no frontal face found — try heads (profiles,
            # downcast, small/far characters).
            return _size_filter(self._predict_boxes(self._head_yolo, image_bgr))

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self._cascade.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=3, minSize=(min_size, min_size)
        )
        return _size_filter([tuple(map(int, f)) for f in faces])

    def _predict_boxes(self, model, image_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        try:
            results = model.predict(
                image_bgr, conf=self.conf, verbose=False, device=self._device,
            )
        except Exception:
            results = model.predict(image_bgr, conf=self.conf, verbose=False, device="cpu")
        boxes = getattr(results[0], "boxes", None)
        if boxes is None:
            return []
        xyxy = boxes.xyxy
        xyxy = xyxy.cpu().numpy() if hasattr(xyxy, "cpu") else np.asarray(xyxy)
        out: list[tuple[int, int, int, int]] = []
        for (x1, y1, x2, y2) in xyxy:
            w = int(round(float(x2) - float(x1)))
            h = int(round(float(y2) - float(y1)))
            out.append((int(round(float(x1))), int(round(float(y1))), w, h))
        return out

    def crop_faces(self, image_bgr: np.ndarray, pad: float = 0.25) -> list[np.ndarray]:
        h, w = image_bgr.shape[:2]
        crops: list[np.ndarray] = []
        for (x, y, fw, fh) in self.detect(image_bgr):
            px = int(fw * pad)
            py = int(fh * pad)
            x0 = max(0, x - px)
            y0 = max(0, y - py)
            x1 = min(w, x + fw + px)
            y1 = min(h, y + fh + py)
            crop = image_bgr[y0:y1, x0:x1]
            if crop.size > 0:
                crops.append(crop)
        return crops


def smart_portrait_crop(image_bgr: np.ndarray) -> np.ndarray:
    """Fallback crop for character reference images when face detection
    fails. Jikan/MAL portraits are always centered with white margins, so
    keeping the upper-center region reliably isolates the character and
    removes most of the background padding.
    """
    h, w = image_bgr.shape[:2]
    y0 = int(h * 0.03)
    y1 = int(h * 0.78)
    x0 = int(w * 0.15)
    x1 = int(w * 0.85)
    crop = image_bgr[y0:y1, x0:x1]
    return crop if crop.size > 0 else image_bgr
