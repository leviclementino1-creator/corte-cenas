from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class CharacterEntry:
    id: int
    name: str
    centroid: np.ndarray   # shape (d,)
    threshold: float


def build_centroid(reference_embeddings: np.ndarray) -> np.ndarray | None:
    """Mean of reference vectors, re-normalized."""
    if reference_embeddings.size == 0:
        return None
    c = reference_embeddings.mean(axis=0)
    n = np.linalg.norm(c)
    if n < 1e-8:
        return None
    return (c / n).astype(np.float32)


class CharacterMatcher:
    """Matches a query embedding against a set of character centroids.

    Returns, per shot, the best confidence per character (above that
    character's threshold). A shot is allowed to carry multiple characters.
    """

    def __init__(self, entries: list[CharacterEntry]) -> None:
        self.entries = [e for e in entries if e.centroid is not None and e.centroid.size > 0]
        if self.entries:
            self.matrix = np.stack([e.centroid for e in self.entries], axis=0)
        else:
            self.matrix = np.zeros((0, 0), dtype=np.float32)

    def score(self, query_embeddings: np.ndarray) -> dict[int, float]:
        """Given N query vectors (e.g. keyframes + face crops for one shot),
        return best cosine per character id.
        """
        if self.matrix.size == 0 or query_embeddings.size == 0:
            return {}
        # query_embeddings is (Q, D), matrix is (C, D). Both L2-normalized.
        sims = query_embeddings @ self.matrix.T  # (Q, C)
        best = sims.max(axis=0)                  # (C,)
        return {self.entries[i].id: float(best[i]) for i in range(len(self.entries))}

    def assign(self, query_embeddings: np.ndarray) -> list[tuple[int, float]]:
        """Return (character_id, confidence) for each character passing threshold."""
        scores = self.score(query_embeddings)
        out: list[tuple[int, float]] = []
        for e in self.entries:
            s = scores.get(e.id, 0.0)
            if s >= e.threshold:
                out.append((e.id, s))
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    def assign_best_per_query(
        self, query_embeddings: np.ndarray, margin: float = 0.0
    ) -> list[tuple[int, float]]:
        """For each query vector, pick the single best character above its
        threshold. If `margin > 0`, the best character must beat the 2nd
        best by at least `margin` or the vote is discarded (kills ambiguous
        matches, common for characters absent from the episode).
        """
        if self.matrix.size == 0 or query_embeddings.size == 0:
            return []
        sims = query_embeddings @ self.matrix.T  # (Q, C)
        best: dict[int, float] = {}
        for q in range(sims.shape[0]):
            row = sims[q]
            order = np.argsort(-row)
            c_idx = int(order[0])
            conf = float(row[c_idx])
            entry = self.entries[c_idx]
            if conf < entry.threshold:
                continue
            if margin > 0.0 and row.shape[0] > 1:
                second = float(row[int(order[1])])
                if conf - second < margin:
                    continue
            prev = best.get(entry.id, 0.0)
            if conf > prev:
                best[entry.id] = conf
        return sorted(best.items(), key=lambda x: x[1], reverse=True)
