"""Optional cross encoder reranking of the fused candidate pool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .hybrid import ScoredChunk


class Reranker(Protocol):
    def rerank(self, query: str, items: list[ScoredChunk]) -> list[ScoredChunk]: ...


class CrossEncoderReranker:
    """Blends cross encoder relevance with the fused score so section priors are not discarded."""

    def __init__(self, model_name: str, blend: float = 0.6) -> None:
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name)
        self.blend = blend

    def rerank(self, query: str, items: list[ScoredChunk]) -> list[ScoredChunk]:
        if not items:
            return items
        import numpy as np

        raw = np.asarray(self.model.predict([(query, it.chunk.text) for it in items]), dtype=float)
        norm = (raw - raw.min()) / (raw.max() - raw.min() + 1e-9)
        for it, s in zip(items, norm, strict=True):
            it.score = round(self.blend * float(s) + (1 - self.blend) * it.score, 6)
        return sorted(items, key=lambda r: -r.score)
