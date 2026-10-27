"""PostgreSQL + pgvector adapter parity tests. Run with a live database:

    SFB_DATABASE_URL=postgresql+psycopg://shortfall:shortfall@localhost:5432/shortfall pytest -m integration
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import SectionType, ShortageStatus
from shortfall_bridge.embeddings import HashingEmbedder
from shortfall_bridge.ingestion.pipeline import ingest_label_records
from shortfall_bridge.sources.openfda import parse_shortage_record
from shortfall_bridge.sources.orange_book import iter_products

from .conftest import FIXTURES

URL = os.environ.get("SFB_DATABASE_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="SFB_DATABASE_URL not set")]


@pytest.fixture(scope="module")
def pg_store():
    from sqlalchemy import create_engine, text

    from shortfall_bridge.store.postgres import PostgresStore

    schema = "t_" + uuid.uuid4().hex[:8]
    admin = create_engine(URL)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    url = URL + ("&" if "?" in URL else "?") + f"options=-csearch_path%3D{schema},public"
    store = PostgresStore(url, dim=384)
    settings = Settings(store_backend="postgres", embedding_backend="hashing", log_json=False)
    ingest_label_records(store, HashingEmbedder(), settings, json.loads((FIXTURES / "labels.json").read_text()))
    store.replace_shortages([parse_shortage_record(x) for x in json.loads((FIXTURES / "shortages.json").read_text())])
    store.replace_orange_book(list(iter_products(FIXTURES / "orange_book_products.txt")))
    store.commit()
    yield store
    with admin.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))


def test_counts(pg_store) -> None:
    s = pg_store.stats()
    assert s["labels"] == 13 and s["shortage_records"] == 3 and s["orange_book_products"] == 7


def test_hybrid_primitives(pg_store) -> None:
    lex = pg_store.lexical_search("clarithromycin contraindicated", 5, ingredients=["simvastatin"])
    assert lex
    vec = pg_store.vector_search(HashingEmbedder().embed_query("renal impairment"), 5, ingredients=["rosuvastatin"])
    assert vec
    secs = pg_store.section_chunks("furosemide", [SectionType.CONTRAINDICATIONS])
    assert secs and all(c.section is SectionType.CONTRAINDICATIONS for c in secs)


def test_shortage_queries(pg_store) -> None:
    _, total = pg_store.shortages(status=ShortageStatus.CURRENT)
    assert total == 2
    assert pg_store.shortages_for_ingredient("furosemide")[0].status is ShortageStatus.CURRENT


def test_classes_from_labels(pg_store) -> None:
    classes = pg_store.classes_for("bumetanide")
    assert any(c.class_type == "EPC" for c in classes)
    assert "furosemide" in pg_store.ingredients_in_class(classes[0].class_id)


def test_engine_parity_on_postgres(pg_store) -> None:
    from shortfall_bridge.api.container import Container
    from shortfall_bridge.domain.models import DoseInput, PatientContext, SubstitutionRequest

    settings = Settings(store_backend="postgres", embedding_backend="hashing", log_json=False)
    c = Container.build(settings, store=pg_store, embedder=HashingEmbedder(), online_resolution=False)
    rec = c.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", current_dose=DoseInput(amount=40),
        patient=PatientContext(current_medications=["clarithromycin"]),
    ))
    bum = next(x for x in rec.candidates if x.ingredient == "bumetanide")
    assert bum.dose_conversion.suggested_dose.startswith("2 mg")
    assert pg_store.get_audit(rec.request_id) is not None
    rec2 = c.service.recommend(SubstitutionRequest(
        drug="atorvastatin", route="oral", patient=PatientContext(current_medications=["clarithromycin"])))
    assert any(x.ingredient == "simvastatin" for x in rec2.not_recommended)
