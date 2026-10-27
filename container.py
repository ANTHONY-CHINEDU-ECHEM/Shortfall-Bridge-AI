"""Dependency container wiring adapters to the engine (composition root)."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..embeddings import Embedder, build_embedder
from ..engine.resolver import DrugResolver
from ..engine.service import SubstitutionService
from ..generation.composer import NarrativeComposer
from ..generation.llm import build_llm
from ..generation.verifier import GroundingVerifier
from ..retrieval.hybrid import HybridRetriever
from ..retrieval.rerank import CrossEncoderReranker
from ..store.base import KnowledgeStore, build_store


@dataclass
class Container:
    settings: Settings
    store: KnowledgeStore
    embedder: Embedder
    service: SubstitutionService
    retriever: HybridRetriever

    @classmethod
    def build(
        cls,
        settings: Settings,
        store: KnowledgeStore | None = None,
        embedder: Embedder | None = None,
        online_resolution: bool = True,
    ) -> Container:
        embedder = embedder or build_embedder(settings)
        store = store or build_store(settings, embedder)
        reranker = (
            CrossEncoderReranker(settings.reranker_model)
            if settings.reranker_enabled and settings.reranker_model else None
        )
        retriever = HybridRetriever(store, embedder, settings, reranker)
        rxnav = None
        if online_resolution:
            from ..sources.http import HttpClient
            from ..sources.rxnav import RxNavClient

            http = HttpClient(settings, settings.data_dir / "cache" / "rxnav", settings.rxnav_requests_per_second)
            rxnav = RxNavClient(http, settings)
        resolver = DrugResolver(store, rxnav)
        composer = NarrativeComposer(build_llm(settings), GroundingVerifier(store.known_ingredients))
        service = SubstitutionService(store, embedder, retriever, resolver, composer, settings)
        return cls(settings=settings, store=store, embedder=embedder, service=service, retriever=retriever)
