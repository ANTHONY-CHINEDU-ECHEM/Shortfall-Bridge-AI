"""Ingestion orchestration.

Each source has an idempotent job. Jobs are restartable: label partitions are cached on disk,
labels upsert by set id keeping the newest version, shortages and the Orange Book are replaced
atomically, and every run is recorded in ``ingestion_runs`` so the API can report data
freshness alongside every recommendation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..domain.enums import SectionType
from ..domain.models import Chunk, LabelDocument
from ..domain.normalize import active_moiety
from ..embeddings import Embedder
from ..logging_setup import log_event
from ..sources import dailymed, openfda, orange_book
from ..sources.http import HttpClient
from ..sources.rxnav import RxNavClient
from ..store.base import KnowledgeStore
from .chunking import chunk_label

logger = logging.getLogger(__name__)

DEFAULT_PRODUCT_TYPES = {"HUMAN PRESCRIPTION DRUG", "HUMAN OTC DRUG"}


@dataclass
class LabelIngestStats:
    seen: int = 0
    indexed: int = 0
    skipped_no_identity: int = 0
    skipped_product_type: int = 0
    chunks: int = 0
    seconds: float = 0.0
    sections: dict[str, int] = field(default_factory=dict)


class LabelBatcher:
    """Accumulates parsed labels and embeds their chunks in large batches for throughput."""

    def __init__(self, store: KnowledgeStore, embedder: Embedder, settings: Settings, stats: LabelIngestStats) -> None:
        self.store, self.embedder, self.settings, self.stats = store, embedder, settings, stats
        self.pending: list[tuple[LabelDocument, list[Chunk]]] = []
        self.pending_chunks = 0

    def add(self, doc: LabelDocument, sections: dict[SectionType, str]) -> None:
        chunks = chunk_label(doc, sections, self.settings.chunk_target_tokens, self.settings.chunk_overlap_tokens)
        if not chunks:
            return
        for s in sections:
            self.stats.sections[s.value] = self.stats.sections.get(s.value, 0) + 1
        self.pending.append((doc, chunks))
        self.pending_chunks += len(chunks)
        if self.pending_chunks >= self.settings.embedding_batch_size * 16:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        texts = [f"{c.title}. {c.section.value.replace('_', ' ')}: {c.text}" for _, cs in self.pending for c in cs]
        vectors = self.embedder.embed_documents(texts)
        offset = 0
        for doc, chunks in self.pending:
            n = len(chunks)
            self.store.upsert_label(doc, chunks, vectors[offset : offset + n])
            offset += n
            self.stats.indexed += 1
            self.stats.chunks += n
        self.store.commit()
        log_event(logger, "label_batch_indexed", labels=len(self.pending), chunks=offset, total_indexed=self.stats.indexed)
        self.pending, self.pending_chunks = [], 0


def ingest_label_records(
    store: KnowledgeStore,
    embedder: Embedder,
    settings: Settings,
    records: Iterable[dict],
    product_types: set[str] | None = None,
    max_records: int | None = None,
) -> LabelIngestStats:
    stats = LabelIngestStats()
    started = time.monotonic()
    batcher = LabelBatcher(store, embedder, settings, stats)
    allowed = product_types if product_types is not None else DEFAULT_PRODUCT_TYPES
    for rec in records:
        if max_records and stats.seen >= max_records:
            break
        stats.seen += 1
        parsed = openfda.parse_label_record(rec)
        if parsed is None:
            stats.skipped_no_identity += 1
            continue
        doc, sections = parsed
        if allowed and doc.product_type and doc.product_type.upper() not in allowed:
            stats.skipped_product_type += 1
            continue
        batcher.add(doc, sections)
    batcher.flush()
    stats.seconds = round(time.monotonic() - started, 1)
    store.record_ingestion_run("openfda_label", stats.indexed, {**stats.__dict__})
    store.commit()
    return stats


def ingest_label_partitions(
    store: KnowledgeStore,
    embedder: Embedder,
    settings: Settings,
    client: HttpClient,
    max_partitions: int | None = None,
    max_records: int | None = None,
    local_paths: list[Path] | None = None,
) -> LabelIngestStats:
    paths = local_paths or openfda.download_label_partitions(
        client, settings, settings.data_dir / "raw" / "openfda_label", max_partitions
    )

    def records() -> Iterable[dict]:
        for p in paths:
            log_event(logger, "label_partition_start", file=p.name)
            yield from openfda.iter_label_json(p)

    return ingest_label_records(store, embedder, settings, records(), max_records=max_records)


def ingest_dailymed_bulk(
    store: KnowledgeStore, embedder: Embedder, settings: Settings, zip_paths: list[Path], max_records: int | None = None
) -> LabelIngestStats:
    stats = LabelIngestStats()
    batcher = LabelBatcher(store, embedder, settings, stats)
    for zp in zip_paths:
        for xml in dailymed.iter_spl_bulk(zp):
            if max_records and stats.seen >= max_records:
                break
            stats.seen += 1
            parsed = dailymed.parse_spl_xml(xml)
            if parsed is None:
                stats.skipped_no_identity += 1
                continue
            batcher.add(*parsed)
    batcher.flush()
    store.record_ingestion_run("dailymed_spl", stats.indexed, {**stats.__dict__})
    store.commit()
    return stats


def ingest_shortages(store: KnowledgeStore, settings: Settings, client: HttpClient) -> int:
    parsed = []
    raw = 0
    for rec in openfda.iter_shortages_api(client, settings):
        raw += 1
        r = openfda.parse_shortage_record(rec)
        if r:
            parsed.append(r)
    n = store.replace_shortages(parsed)
    store.record_ingestion_run("fda_drug_shortages", n, {"raw_records": raw})
    store.commit()
    log_event(logger, "shortages_ingested", records=n, raw=raw)
    return n


def ingest_orange_book(store: KnowledgeStore, settings: Settings, client: HttpClient, local_path: Path | None = None) -> int:
    path = local_path or orange_book.download_orange_book(client, settings, settings.data_dir / "raw" / "orange_book")
    products = list(orange_book.iter_products(path))
    n = store.replace_orange_book(products)
    store.record_ingestion_run("orange_book", n, {"file": str(path)})
    store.commit()
    log_event(logger, "orange_book_ingested", products=n)
    return n


def enrich_classes(
    store: KnowledgeStore, rxnav: RxNavClient, ingredients: Iterable[str] | None = None, limit: int | None = None
) -> int:
    """Attach ATC and DailyMed EPC classes from RxClass to each ingredient moiety."""
    targets = sorted({active_moiety(i) for i in (ingredients or store.known_ingredients())})
    if limit:
        targets = targets[:limit]
    linked = 0
    for i, ing in enumerate(targets, 1):
        try:
            rxcui = rxnav.rxcui_for_ingredient(ing)
            if not rxcui:
                continue
            classes = rxnav.classes_for_rxcui(rxcui)
            if classes:
                store.upsert_classes(ing, classes)
                linked += len(classes)
        except Exception as exc:  # one bad concept must not stop a 10k ingredient run
            log_event(logger, "class_enrichment_failed", level=logging.WARNING, ingredient=ing, error=str(exc))
        if i % 200 == 0:
            store.commit()
            log_event(logger, "class_enrichment_progress", done=i, total=len(targets), links=linked)
    store.record_ingestion_run("rxclass", linked, {"ingredients": len(targets)})
    store.commit()
    return linked

