"""Knowledge store port.

The engine depends only on this protocol. Two adapters implement it:

* ``PostgresStore``: production. PostgreSQL 16 with pgvector (HNSW, cosine) for dense search and
  a generated ``tsvector`` column with GIN index for lexical search, so hybrid retrieval, metadata
  filtering, shortage joins and the audit log live in one transactional database.
* ``MemoryStore``: tests and single machine evaluation. Same semantics, numpy and an inverted
  index, persisted as a snapshot directory.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..config import Settings
from ..domain.enums import SectionType, ShortageStatus
from ..domain.models import (
    Chunk,
    DrugClass,
    LabelDocument,
    OrangeBookProduct,
    ShortageRecord,
)


def text_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:20]


@runtime_checkable
class KnowledgeStore(Protocol):
    # Writes -----------------------------------------------------------------------------------
    def upsert_label(self, doc: LabelDocument, chunks: Sequence[Chunk], embeddings: np.ndarray) -> None: ...
    def replace_shortages(self, records: Sequence[ShortageRecord]) -> int: ...
    def replace_orange_book(self, products: Sequence[OrangeBookProduct]) -> int: ...
    def upsert_classes(self, ingredient: str, classes: Sequence[DrugClass]) -> None: ...
    def record_ingestion_run(self, source: str, records: int, details: dict[str, Any]) -> None: ...
    def record_audit(self, request_id: str, request: dict[str, Any], response: dict[str, Any]) -> None: ...
    def commit(self) -> None: ...

    # Reads: entities --------------------------------------------------------------------------
    def search_ingredients(self, term: str, limit: int = 10) -> list[tuple[str, str, float]]: ...
    def labels_for_ingredient(self, ingredient: str, limit: int = 50) -> list[LabelDocument]: ...
    def label(self, set_id: str) -> LabelDocument | None: ...
    def chunks(self, chunk_ids: Sequence[str]) -> list[Chunk]: ...
    def classes_for(self, ingredient: str) -> list[DrugClass]: ...
    def ingredients_in_class(self, class_id: str, limit: int = 100) -> list[str]: ...
    def shortages(
        self, status: ShortageStatus | None = None, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[ShortageRecord], int]: ...
    def shortages_for_ingredient(self, ingredient: str) -> list[ShortageRecord]: ...
    def orange_book_for_ingredient(self, ingredient: str) -> list[OrangeBookProduct]: ...
    def known_ingredients(self) -> set[str]: ...

    # Reads: retrieval -------------------------------------------------------------------------
    def lexical_search(
        self,
        query: str,
        k: int,
        ingredients: Sequence[str] | None = None,
        sections: Sequence[SectionType] | None = None,
    ) -> list[tuple[str, float]]: ...

    def vector_search(
        self,
        vector: np.ndarray,
        k: int,
        ingredients: Sequence[str] | None = None,
        sections: Sequence[SectionType] | None = None,
    ) -> list[tuple[str, float]]: ...

    def section_chunks(self, ingredient: str, sections: Sequence[SectionType], limit: int = 40) -> list[Chunk]: ...

    # Ops --------------------------------------------------------------------------------------
    def stats(self) -> dict[str, int]: ...
    def data_versions(self) -> dict[str, str]: ...
    def get_audit(self, request_id: str) -> dict[str, Any] | None: ...


def build_store(settings: Settings) -> KnowledgeStore:
    if settings.store_backend == "memory":
        from .memory import MemoryStore

        store = MemoryStore(dim=settings.embedding_dim)
        if settings.snapshot_path and settings.snapshot_path.exists():
            store.load(settings.snapshot_path)
        return store
    from .postgres import PostgresStore

    return PostgresStore(settings.database_url, dim=settings.embedding_dim)
