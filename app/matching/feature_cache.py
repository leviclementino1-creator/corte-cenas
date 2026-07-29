"""Cache persistente das features CARAS (boxes do YOLO + embeddings CLIP).

O problema: toda reanálise re-rodava YOLO + CLIP em cada keyframe — minutos
de GPU pra recomputar exatamente os mesmos números, já que os keyframes vêm
dos cortes cacheados e não mudam. Mudar um threshold, refazer com "Adicionar",
ou reanalisar depois de um bloqueio manual pagava o preço inteiro de novo.

A solução: um .npz por episódio (boxes + embeddings + flag de créditos por
keyframe) e um por anime (embeddings das referências). Entrada validada por
mtime+size do arquivo de origem; o cache inteiro é invalidado se a meta
(modelo CLIP, modelo YOLO, padding do crop...) mudar. Com cache cheio, a
reanálise nem carrega os modelos — vai direto pra matemática do matcher.

Formato: np.savez_compressed com os arrays + um JSON (meta + índice) num
array unicode "__meta__". Nada de pickle — allow_pickle fica False."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

CACHE_VERSION = 1


class FeatureCache:
    """get/put de arrays por (arquivo de origem, tipo de feature).

    Tipos usados hoje: "boxes" (N,4 int32 — brutos, sem padding), "embs"
    (N,D float32 — crops com padding, pareados 1:1 com boxes), "kfemb"
    (1,D — keyframe inteiro, fallback sem rosto) e "credit" (1, uint8).
    """

    def __init__(self, path: Path, meta: dict) -> None:
        self.path = path
        self.meta = {"version": CACHE_VERSION, **meta}
        self._index: dict[str, dict] = {}      # chave -> {id, mtime, size}
        self._arrays: dict[str, np.ndarray] = {}  # "a{id}:{kind}" -> array
        self._next_id = 0
        self.hits = 0
        self.misses = 0
        self._dirty = False     # nada a gravar até o primeiro put()
        self._load()

    # ---------- persistência ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with np.load(self.path, allow_pickle=False) as z:
                header = json.loads(str(z["__meta__"]))
                if header.get("meta") != self.meta:
                    return  # modelo/config mudou — cache inteiro descartado
                arrays = {k: z[k] for k in z.files if k != "__meta__"}
        except Exception as e:
            print(f"[FeatureCache] Cache ilegível ({self.path.name}): {e} — recomeçando.")
            return
        self._index = header.get("index", {})
        self._arrays = arrays
        ids = [ent["id"] for ent in self._index.values()]
        self._next_id = max(ids) + 1 if ids else 0

    def save(self) -> None:
        # Nada mudou desde o último save (reanálise 100% quente): reescrever
        # o arquivo inteiro só gasta disco e tempo.
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            header = json.dumps({"meta": self.meta, "index": self._index})
            # savez SEM compressão: float32 de embedding é praticamente
            # incompressível (medido: 3,6x mais lento pra ~0% de ganho de
            # tamanho). E escrita ATÔMICA: fechar o app no meio de um save
            # corrompia o cache e jogava fora a análise inteira — agora o
            # arquivo velho só é trocado quando o novo está completo.
            tmp = self.path.with_suffix(".npz.tmp")
            np.savez(tmp, __meta__=np.array(header), **self._arrays)
            tmp.replace(self.path)
            self._dirty = False
        except Exception as e:
            print(f"[FeatureCache] Falha ao salvar {self.path.name}: {e}")

    # ---------- acesso ----------

    @staticmethod
    def _stat(file: Path) -> tuple[int, int] | None:
        try:
            st = file.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    @staticmethod
    def _key(file: Path) -> str:
        return str(file).lower()

    def get(self, file: Path, kind: str) -> np.ndarray | None:
        ent = self._index.get(self._key(file))
        if ent is None:
            return None
        st = self._stat(file)
        if st is None or [st[0], st[1]] != [ent["mtime"], ent["size"]]:
            return None  # arquivo regenerado — features velhas não valem
        return self._arrays.get(f"a{ent['id']}:{kind}")

    def put(self, file: Path, kind: str, arr: np.ndarray) -> None:
        st = self._stat(file)
        if st is None:
            return
        key = self._key(file)
        ent = self._index.get(key)
        if ent is None or [st[0], st[1]] != [ent["mtime"], ent["size"]]:
            if ent is not None:
                # arquivo mudou: arrays antigos desse id ficam órfãos — limpa
                stale = f"a{ent['id']}:"
                for k in [k for k in self._arrays if k.startswith(stale)]:
                    del self._arrays[k]
            ent = {"id": self._next_id, "mtime": st[0], "size": st[1]}
            self._index[key] = ent
            self._next_id += 1
        self._arrays[f"a{ent['id']}:{kind}"] = np.ascontiguousarray(arr)
        self._dirty = True

    def stats_line(self) -> str:
        total = self.hits + self.misses
        if total == 0:
            return "cache vazio"
        return f"{self.hits}/{total} em cache"
