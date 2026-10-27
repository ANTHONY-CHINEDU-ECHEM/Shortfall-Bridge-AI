"""Test fixtures.

The fixture files are miniature, openFDA shaped records written for unit tests only. They exist
to exercise parsing and engine logic deterministically and offline; the system itself is built
to ingest the full public openFDA, Orange Book and RxClass corpora (see README, Data section).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortfall_bridge.api.container import Container
from shortfall_bridge.config import Settings
from shortfall_bridge.embeddings import HashingEmbedder
from shortfall_bridge.ingestion.pipeline import ingest_label_records
from shortfall_bridge.sources.openfda import parse_shortage_record
from shortfall_bridge.sources.orange_book import iter_products
from shortfall_bridge.store.memory import MemoryStore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test", store_backend="memory", embedding_backend="hashing", llm_provider="none",
        log_json=False, log_level="WARNING", data_dir=Path("/tmp/sfb_test"), api_rate_limit_per_minute=10_000,
    )


@pytest.fixture(scope="session")
def label_records() -> list[dict]:
    return json.loads((FIXTURES / "labels.json").read_text())


@pytest.fixture(scope="session")
def store(settings: Settings, label_records: list[dict]) -> MemoryStore:
    s = MemoryStore(dim=settings.embedding_dim)
    ingest_label_records(s, HashingEmbedder(settings.embedding_dim), settings, label_records)
    s.replace_shortages(
        [r for r in (parse_shortage_record(x) for x in json.loads((FIXTURES / "shortages.json").read_text())) if r]
    )
    s.replace_orange_book(list(iter_products(FIXTURES / "orange_book_products.txt")))
    return s


@pytest.fixture(scope="session")
def container(settings: Settings, store: MemoryStore) -> Container:
    return Container.build(settings, store=store, embedder=HashingEmbedder(settings.embedding_dim),
                           online_resolution=False)
