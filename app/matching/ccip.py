"""CCIP — segunda opinião local especializada em personagem de anime.

O CLIP decide rápido mas decide RASPANDO: margem média top1−top2 de 0.077 no
gabarito do Mushoku — qualquer ruído flipa o rótulo. O CCIP (deepghs/ccip_onnx,
treinado especificamente pra "esses dois desenhos são o MESMO personagem?")
empata com o CLIP no acerto (0.96 × 0.94) mas decide com 4.3x mais folga
(margem 0.329), ao custo de CPU (~4 rostos/s em ONNX). Por isso ele não
substitui o CLIP: entra só nos pontos frágeis — veto de atribuição apertada,
resgate de duvidosos, âncora de presença e decisão por grupo — onde folga
importa mais que velocidade e cada consulta local é uma pergunta a menos pra
IA paga. Detalhe que o CLIP-de-rosto não tem: o CCIP lê o personagem INTEIRO,
então refs "retrato sem rosto detectável" (o caso Rimuru) ainda rendem vetor.

Extractor mínimo vendorizado do dghs-imgutils (a lib inteira traria sklearn e
pandas pro executável): resize 384 bilinear (PIL, idêntico ao original) +
normalização CLIP-stats + ONNX em CPU. O modelo (~190 MB) baixa do
HuggingFace no primeiro uso, igual ao YOLO — depois fica no cache."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

CCIP_REPO = "deepghs/ccip_onnx"
CCIP_MODEL = "ccip-caformer-24-randaug-pruned"
CCIP_FILE = f"{CCIP_MODEL}/model_feat.onnx"
# Kind no FeatureCache: o nome carrega o modelo — trocar de modelo invalida
# só as linhas CCIP, sem descartar boxes/embeddings CLIP já cacheados.
CCIP_KIND = f"ccip:{CCIP_MODEL}"

_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
_SIZE = 384
_BATCH = 8


def runtime_available() -> tuple[bool, str]:
    """O ambiente consegue rodar CCIP? (import barato, sem carregar modelo)."""
    try:
        import onnxruntime  # noqa: F401
    except Exception as e:  # pragma: no cover - depende do ambiente
        return False, f"onnxruntime indisponível ({e})"
    try:
        from huggingface_hub import hf_hub_download  # noqa: F401
    except Exception as e:  # pragma: no cover
        return False, f"huggingface_hub indisponível ({e})"
    return True, ""


def model_cached() -> bool:
    """True se o modelo já está no cache local (pra só avisar do download
    de ~190 MB quando ele realmente vai acontecer)."""
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(CCIP_REPO, CCIP_FILE), str)
    except Exception:
        return False


class CcipEngine:
    """Extração de vetores CCIP (768-d, L2-normalizados) em CPU."""

    def __init__(self) -> None:
        self._session = None

    def _ensure(self):
        if self._session is None:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(CCIP_REPO, CCIP_FILE)
            self._session = ort.InferenceSession(
                path, providers=["CPUExecutionProvider"]
            )
        return self._session

    @staticmethod
    def _preprocess_rgb(img_rgb: np.ndarray) -> np.ndarray:
        """(H,W,3) RGB uint8 → (3,384,384) float32 normalizado. Resize via
        PIL BILINEAR — bit a bit igual ao dghs-imgutils, que foi quem gerou
        a evidência do A/B; cv2.INTER_LINEAR difere no downscale."""
        from PIL import Image

        pil = Image.fromarray(img_rgb).resize((_SIZE, _SIZE), Image.BILINEAR)
        data = np.asarray(pil).transpose(2, 0, 1).astype(np.float32) / 255.0
        return (data - _MEAN[:, None, None]) / _STD[:, None, None]

    def extract_rgb(self, imgs_rgb: list[np.ndarray]) -> np.ndarray:
        """Lote de imagens RGB → (N, 768) float32, linhas L2-normalizadas."""
        if not imgs_rgb:
            return np.zeros((0, 768), dtype=np.float32)
        sess = self._ensure()
        outs: list[np.ndarray] = []
        for i in range(0, len(imgs_rgb), _BATCH):
            chunk = imgs_rgb[i:i + _BATCH]
            data = np.stack([self._preprocess_rgb(im) for im in chunk])
            (out,) = sess.run(["output"], {"input": data.astype(np.float32)})
            outs.append(out)
        vecs = np.concatenate(outs, axis=0).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-8)

    def extract_bgr(self, imgs_bgr: list[np.ndarray]) -> np.ndarray:
        return self.extract_rgb(
            [cv2.cvtColor(im, cv2.COLOR_BGR2RGB) for im in imgs_bgr]
        )

    def extract_files(
        self, paths: list[Path]
    ) -> tuple[np.ndarray, list[Path]]:
        """Vetores das imagens legíveis + quais caminhos renderam vetor
        (pareados 1:1 com as linhas)."""
        imgs: list[np.ndarray] = []
        ok: list[Path] = []
        for p in paths:
            try:
                img = cv2.imread(str(p))
            except Exception:
                img = None
            if img is None or img.ndim != 3:
                continue
            imgs.append(img)
            ok.append(p)
        return self.extract_bgr(imgs), ok


class CcipBank:
    """Vetores CCIP das refs por personagem + consulta estilo matcher.

    A similaridade de um rosto com um personagem é o MELHOR ref dele (max) —
    mesma agregação da primeira passada do CLIP e do A/B que fundamentou o
    híbrido. Refs viram vetores da imagem INTEIRA (o CCIP foi treinado em
    personagem completo, e é isso que deixa retratos sem rosto utilizáveis).
    """

    def __init__(self) -> None:
        self.char_ids: list[int] = []
        self._starts = np.zeros(0, dtype=np.int64)
        self._matrix = np.zeros((0, 768), dtype=np.float32)

    def build(
        self,
        engine: CcipEngine,
        refs_per_id: dict[int, list[Path]],
        cache,
    ) -> None:
        """Extrai (com cache por arquivo) e monta a matriz contígua."""
        blocks: list[tuple[int, np.ndarray]] = []
        for cid, paths in refs_per_id.items():
            rows: list[np.ndarray] = []
            misses: list[Path] = []
            for p in paths:
                got = cache.get(p, CCIP_KIND) if cache is not None else None
                if got is not None and got.size:
                    rows.append(got)
                else:
                    misses.append(p)
            if misses:
                vecs, ok = engine.extract_files(misses)
                for p, v in zip(ok, vecs):
                    row = v[None, :]
                    rows.append(row)
                    if cache is not None:
                        cache.put(p, CCIP_KIND, row)
            if rows:
                blocks.append((cid, np.concatenate(rows, axis=0)))
        if not blocks:
            return
        self.char_ids = [cid for cid, _ in blocks]
        mats = [m for _, m in blocks]
        self._starts = np.cumsum([0] + [m.shape[0] for m in mats[:-1]])
        self._matrix = np.concatenate(mats, axis=0)

    @property
    def empty(self) -> bool:
        return self._matrix.size == 0

    @property
    def n_refs(self) -> int:
        return int(self._matrix.shape[0]) if self._matrix.size else 0

    def char_sims(self, vecs: np.ndarray) -> np.ndarray:
        """(Q, C): melhor cosseno de cada vetor contra cada personagem."""
        sims = vecs @ self._matrix.T
        return np.maximum.reduceat(sims, self._starts, axis=1)

    def verdict(self, vec: np.ndarray) -> list[tuple[int, float]]:
        """[(char_id, sim)] em ordem decrescente pra UM vetor."""
        if self.empty:
            return []
        row = self.char_sims(vec[None, :])[0]
        order = np.argsort(-row)
        return [(self.char_ids[int(i)], float(row[int(i)])) for i in order]


def face_vectors(
    engine: CcipEngine,
    kf_cache,
    face_refs: list,
    pad: float,
) -> dict[tuple[str, int], np.ndarray]:
    """Vetores CCIP pros rostos pedidos, via proveniência (keyframe, box).

    Computa e cacheia TODOS os boxes de cada keyframe tocado (linhas 1:1 com
    "boxes", mesmo invariante dos embeddings CLIP) — na reanálise quente a
    segunda opinião sai do cache sem abrir imagem nenhuma."""
    from .face_detector import crops_from_boxes

    out: dict[tuple[str, int], np.ndarray] = {}
    wanted: dict[str, set[int]] = {}
    for fr in face_refs:
        if not fr:
            continue
        kf_path, bi = fr
        wanted.setdefault(str(kf_path), set()).add(int(bi))
    for key, indices in wanted.items():
        p = Path(key)
        boxes = kf_cache.get(p, "boxes")
        if boxes is None or not len(boxes):
            continue
        rows = kf_cache.get(p, CCIP_KIND)
        if rows is None or len(rows) != len(boxes):
            try:
                img = cv2.imread(key)
            except Exception:
                img = None
            if img is None:
                continue
            crops, _kept = crops_from_boxes(img, boxes, pad)
            if len(crops) != len(boxes):
                continue
            rows = engine.extract_bgr(crops)
            kf_cache.put(p, CCIP_KIND, rows)
        for bi in indices:
            if bi < len(rows):
                out[(key, bi)] = rows[bi]
    return out
