"""Tamper evident audit chain and pharmacist review records (memory adapter)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from shortfall_bridge.analytics.reviews import review_summary
from shortfall_bridge.domain.enums import ReviewDecision
from shortfall_bridge.domain.models import ReviewRequest
from shortfall_bridge.store.ledger import GENESIS, chain_hash, verify_chain
from shortfall_bridge.store.memory import MemoryStore


def _store_with(n: int) -> MemoryStore:
    s = MemoryStore()
    for i in range(n):
        s.record_audit(f"r_{i}", {"drug": f"d{i}", "dose": 1.5 * i}, {"request_id": f"r_{i}", "score": 0.1 * i})
    return s


def test_chain_links_and_verifies() -> None:
    s = _store_with(4)
    recs = [s.get_audit(f"r_{i}") for i in range(4)]
    assert recs[0]["prev_hash"] == GENESIS
    assert all(recs[i]["prev_hash"] == recs[i - 1]["record_hash"] for i in range(1, 4))
    assert [r["seq"] for r in recs] == [1, 2, 3, 4]
    report = s.verify_audit_chain()
    assert report == {"ok": True, "checked": 4, "legacy_unchained": 0, "head_hash": recs[-1]["record_hash"]}


def test_modification_is_detected() -> None:
    s = _store_with(3)
    s.audit["r_1"]["response"]["score"] = 0.99  # someone edits a stored recommendation
    report = s.verify_audit_chain()
    assert not report["ok"] and report["first_break"] == "r_1" and "modified" in report["reason"]


def test_deletion_is_detected() -> None:
    s = _store_with(3)
    s.audit_order.remove("r_1")
    report = s.verify_audit_chain()
    assert not report["ok"] and report["first_break"] == "r_2" and "removed" in report["reason"]


def test_records_are_immutable() -> None:
    s = _store_with(1)
    first = s.get_audit("r_0")["record_hash"]
    s.record_audit("r_0", {"drug": "other"}, {"x": 1})
    assert s.get_audit("r_0")["record_hash"] == first and len(s.audit_order) == 1


def test_legacy_rows_are_reported_not_failed() -> None:
    h = chain_hash(GENESIS, "b", {}, {}, "t")
    rows = [{"request_id": "a", "request": {}, "response": {}, "created_at": "t", "prev_hash": None, "record_hash": None},
            {"request_id": "b", "request": {}, "response": {}, "created_at": "t", "prev_hash": GENESIS, "record_hash": h}]
    assert verify_chain(rows) == {"ok": True, "checked": 1, "legacy_unchained": 1, "head_hash": h}


def test_snapshot_round_trip_keeps_chain_and_reviews(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from shortfall_bridge.domain.models import ReviewRecord

    s = _store_with(3)
    s.record_review(ReviewRecord(request_id="r_1", candidate_ingredient="xylitol", decision=ReviewDecision.AGREE,
                                 review_id="rv_1", created_at=datetime.now(UTC), rank=1))
    s.save(tmp_path)
    clone = MemoryStore()
    clone.load(tmp_path)
    assert clone.verify_audit_chain()["ok"] and clone.verify_audit_chain()["checked"] == 3
    assert [r.review_id for r in clone.reviews()] == ["rv_1"]


def test_override_requires_reason() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(request_id="r_1", candidate_ingredient="bumetanide", decision=ReviewDecision.OVERRIDE)
    ReviewRequest(request_id="r_1", candidate_ingredient="bumetanide", decision=ReviewDecision.OVERRIDE, reason="no stock")


def test_review_summary_metrics() -> None:
    from datetime import UTC, datetime

    from shortfall_bridge.domain.enums import CandidateTier
    from shortfall_bridge.domain.models import ReviewRecord

    def rv(i: int, decision: ReviewDecision, rank: int, tier: CandidateTier, reason: str | None = None,
           chosen: str | None = None) -> ReviewRecord:
        return ReviewRecord(request_id=f"r_{i % 2}", candidate_ingredient=f"drug{rank}", candidate_tier=tier,
                            decision=decision, reason=reason, chosen_alternative=chosen, review_id=f"rv_{i}",
                            created_at=datetime.now(UTC), rank=rank)

    T1, T3 = CandidateTier.THERAPEUTIC_EQUIVALENT, CandidateTier.THERAPEUTIC_INTERCHANGE
    out = review_summary([
        rv(0, ReviewDecision.AGREE, 1, T1), rv(1, ReviewDecision.AGREE, 1, T1),
        rv(2, ReviewDecision.OVERRIDE, 1, T1, "No stock locally", "bumetanide"),
        rv(3, ReviewDecision.REJECT, 2, T3, "No stock  locally"),
    ])
    assert out["reviews"] == 4 and out["requests_reviewed"] == 2
    assert out["agreement_rate"] == 0.5 and out["top_option_agreement_rate"] == pytest.approx(0.667, abs=0.001)
    assert out["by_tier"][T1.value]["agreement_rate"] == pytest.approx(0.667, abs=0.001)
    assert out["top_override_reasons"][0] == {"reason": "no stock locally", "count": 2}
    assert out["alternatives_chosen_instead"] == [{"ingredient": "bumetanide", "count": 1}]
    assert review_summary([])["agreement_rate"] is None
