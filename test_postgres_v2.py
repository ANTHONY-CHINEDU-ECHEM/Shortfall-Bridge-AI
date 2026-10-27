"""PostgreSQL parity for v2 governance features: audit hash chain, tamper detection, legacy rows, reviews.

    SFB_DATABASE_URL=postgresql+psycopg://shortfall:shortfall@localhost:5432/shortfall pytest -m integration
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from shortfall_bridge.domain.enums import CandidateTier, ReviewDecision
from shortfall_bridge.domain.models import ReviewRecord

URL = os.environ.get("SFB_DATABASE_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="SFB_DATABASE_URL not set")]


@pytest.fixture()
def pg():
    from sqlalchemy import create_engine, text

    from shortfall_bridge.store.postgres import PostgresStore

    schema = "t_" + uuid.uuid4().hex[:8]
    admin = create_engine(URL)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    url = URL + ("&" if "?" in URL else "?") + f"options=-csearch_path%3D{schema},public"
    yield PostgresStore(url, dim=384), url, schema, admin
    with admin.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))


def test_chain_on_jsonb_round_trip(pg) -> None:
    store, *_ = pg
    payloads = [({"drug": "furosemide", "dose": 0.1 * i, "unicode": "≤ 30 mL/min", "nested": {"b": [1, 2], "a": None}},
                 {"request_id": f"r_{i}", "score": 0.6733, "big": 1e-05}) for i in range(3)]
    for i, (q, r) in enumerate(payloads):
        store.record_audit(f"r_{i}", q, r)
    report = store.verify_audit_chain()
    assert report["ok"] and report["checked"] == 3
    rec = store.get_audit("r_2")
    assert rec["seq"] and rec["prev_hash"] == store.get_audit("r_1")["record_hash"]
    assert report["head_hash"] == rec["record_hash"]


def test_tampering_detected_in_database(pg) -> None:
    from sqlalchemy import text

    store, *_ = pg
    for i in range(3):
        store.record_audit(f"r_{i}", {"i": i}, {"request_id": f"r_{i}", "status": "ok"})
    with store.engine.begin() as c:
        c.execute(text("UPDATE recommendation_audit SET response = jsonb_set(response, '{status}', '\"abstain\"') "
                       "WHERE request_id = 'r_1'"))
    report = store.verify_audit_chain()
    assert not report["ok"] and report["first_break"] == "r_1"


def test_legacy_rows_then_chain(pg) -> None:
    from sqlalchemy import text

    store, *_ = pg
    with store.engine.begin() as c:  # a row written by v1 (no hash columns populated)
        c.execute(text("INSERT INTO recommendation_audit (request_id, request, response) "
                       "VALUES ('legacy', '{}'::jsonb, '{}'::jsonb)"))
    store.record_audit("r_new", {"a": 1}, {"b": 2})
    report = store.verify_audit_chain()
    assert report["ok"] and report["legacy_unchained"] == 1 and report["checked"] == 1


def test_schema_upgrade_is_idempotent(pg) -> None:
    store, url, *_ = pg
    from shortfall_bridge.store.postgres import PostgresStore

    store.record_audit("r_a", {}, {})
    again = PostgresStore(url, dim=384)  # re-runs schema.sql including the v2 ALTERs
    again.record_audit("r_b", {}, {})
    assert again.verify_audit_chain()["checked"] == 2


def test_reviews_round_trip(pg) -> None:
    store, *_ = pg
    for i, decision in enumerate([ReviewDecision.AGREE, ReviewDecision.OVERRIDE]):
        store.record_review(ReviewRecord(
            request_id="r_1", candidate_ingredient="bumetanide", candidate_tier=CandidateTier.THERAPEUTIC_INTERCHANGE,
            decision=decision, reason=None if decision is ReviewDecision.AGREE else "No IV stock",
            review_id=f"rv_{i}", created_at=datetime.now(UTC), rank=2))
    rows = store.reviews("r_1")
    assert {r.review_id for r in rows} == {"rv_0", "rv_1"}
    assert next(r for r in rows if r.review_id == "rv_1").reason == "No IV stock"
    assert store.reviews("other") == [] and len(store.reviews()) == 2
