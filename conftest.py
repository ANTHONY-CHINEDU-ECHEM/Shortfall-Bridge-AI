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


def make_label(
    set_id: str, substance: str, sections: dict[str, str], epc: str | None = None, route: str = "ORAL",
    manufacturer: str = "Test Labs", effective: str = "20250101", product_type: str = "HUMAN PRESCRIPTION DRUG",
) -> dict:
    """An openFDA shaped label record for tests that need label text the shared fixtures do not carry."""
    return {
        "set_id": set_id, "id": f"{set_id}-v", "version": "1", "effective_time": effective,
        "openfda": {
            "generic_name": [substance.upper()], "brand_name": [substance.title()],
            "manufacturer_name": [manufacturer], "substance_name": [substance.upper()], "route": [route],
            "product_type": [product_type], "rxcui": [], "pharm_class_epc": [epc] if epc else [], "pharm_class_moa": [],
        },
        **{k: [v] for k, v in sections.items()},
    }


def build_store(settings: Settings, records: list[dict], shortages: list[dict] | None = None,
                orange_book: Path | None = None) -> MemoryStore:
    s = MemoryStore(dim=settings.embedding_dim)
    ingest_label_records(s, HashingEmbedder(settings.embedding_dim), settings, records)
    if shortages is not None:
        s.replace_shortages([r for r in (parse_shortage_record(x) for x in shortages) if r])
    if orange_book is not None:
        s.replace_orange_book(list(iter_products(orange_book)))
    return s


def build_container(settings: Settings, store: MemoryStore) -> Container:
    return Container.build(settings, store=store, embedder=HashingEmbedder(settings.embedding_dim),
                           online_resolution=False)
