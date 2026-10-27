"""Hybrid retrieval: BM25 and dense search fused with reciprocal rank fusion plus section priors.

Why hybrid: label language mixes exact tokens that dense models blur (drug names, "CrCl less than
30 mL/min", CYP3A4) with paraphrase that lexical search misses ("kidney function" versus "renal
impairment"). Reciprocal rank fusion is parameter light and robust to the very different score
scales of ts_rank and cosine similarity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..config import Settings
from ..domain.enums import SectionType
from ..domain.models import Chunk
from ..embeddings import Embedder
from ..store.base import KnowledgeStore
from .rerank import Reranker


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    lexical_rank: int | None
    dense_rank: int | None
    section_prior: float


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = 60, weights: Sequence[float] | None = None
) -> dict[str, float]:
    fused: dict[str, float] = {}
    weights = weights or [1.0] * len(rankings)
    for ranking, w in zip(rankings, weights, strict=True):
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + w / (k + rank)
    return fused


class HybridRetriever:
    def __init__(
        self, store: KnowledgeStore, embedder: Embedder, settings: Settings, reranker: Reranker | None = None
    ) -> None:
        self.store, self.embedder, self.settings, self.reranker = store, embedder, settings, reranker

    def retrieve(
        self,
        query: str,
        k: int | None = None,
        ingredients: Sequence[str] | None = None,
        sections: Sequence[SectionType] | None = None,
        priors: dict[SectionType, float] | None = None,
        prior_weight: float = 0.35,
    ) -> list[ScoredChunk]:
        k = k or self.settings.retrieval_top_k
        pool = k * 4
        lexical = self.store.lexical_search(query, pool, ingredients, sections)
        dense = self.store.vector_search(self.embedder.embed_query(query), pool, ingredients, sections)
        lex_ids = [c for c, _ in lexical]
        dense_ids = [c for c, _ in dense]
        fused = reciprocal_rank_fusion([lex_ids, dense_ids], k=self.settings.rrf_k)
        if not fused:
            return []
        chunks = {c.chunk_id: c for c in self.store.chunks(list(fused))}
        max_fused = max(fused.values())
        results: list[ScoredChunk] = []
        for cid, score in fused.items():
            chunk = chunks.get(cid)
            if chunk is None:
                continue
            prior = (priors or {}).get(chunk.section, 0.0)
            final = (score / max_fused) * (1 - prior_weight) + prior * prior_weight
            results.append(
                ScoredChunk(
                    chunk=chunk,
                    score=round(final, 6),
                    lexical_rank=lex_ids.index(cid) + 1 if cid in lex_ids else None,
                    dense_rank=dense_ids.index(cid) + 1 if cid in dense_ids else None,
                    section_prior=prior,
                )
            )
        results.sort(key=lambda r: -r.score)
        if self.reranker is not None:
            results = self.reranker.rerank(query, results[: pool])
        return results[:k]
