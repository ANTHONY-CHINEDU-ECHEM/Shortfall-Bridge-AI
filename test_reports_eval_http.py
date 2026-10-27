"""Insights computation, evaluation runner extensions, HTTP cache TTL and name resolution with strengths."""

import json
import os
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

from shortfall_bridge.analytics.insights import shortage_insights
from shortfall_bridge.api.container import Container
from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import ShortageStatus
from shortfall_bridge.domain.models import ShortageRecord, SubstitutionRequest
from shortfall_bridge.domain.normalize import ingredients_from_shortage_name, word_boundary_contains
from shortfall_bridge.evaluation.runner import junit_xml, run_suite
from shortfall_bridge.sources.http import HttpClient


def _rec(i: int, status: ShortageStatus, posted: date | None, updated: date | None, cat: str, reason: str | None,
         form: str = "Injection") -> ShortageRecord:
    return ShortageRecord(record_id=f"s{i}", generic_name=f"Drug{i} {form}", ingredients=[f"drug{i % 2}"],
                          company_name=f"Maker{i % 3}", dosage_form=form, status=status, shortage_reason=reason,
                          therapeutic_category=[cat], initial_posting_date=posted, update_date=updated)


def test_shortage_insights_aggregates() -> None:
    today = date(2026, 10, 27)
    records = [
        _rec(1, ShortageStatus.CURRENT, date(2026, 1, 10), date(2026, 10, 1), "Cardiology", "Demand increase"),
        _rec(2, ShortageStatus.CURRENT, date(2025, 10, 27), date(2026, 9, 15), "Cardiology", None, "Tablet"),
        _rec(3, ShortageStatus.CURRENT, date(2026, 9, 1), date(2026, 10, 20), "Anesthesia", "Demand increase"),
        _rec(4, ShortageStatus.RESOLVED, date(2024, 1, 1), date(2025, 2, 1), "Oncology", "Manufacturing delays"),
    ]
    out = shortage_insights(records, today=today, months=12)
    cur = out["current"]
    assert out["by_status"] == {"Current": 3, "Resolved": 1} and cur["entries"] == 3
    assert cur["by_category"][0] == {"name": "Cardiology", "count": 2}
    assert cur["by_reason"][0] == {"name": "Demand increase", "count": 2}
    assert {"name": "Not stated", "count": 1} in cur["by_reason"]
    assert {"name": "oral", "count": 1} in cur["by_route_family"] and {"name": "parenteral", "count": 2} in cur["by_route_family"]
    assert cur["days_listed"]["max"] == 365 and cur["longest_running"][0]["record_id"] == "s2"
    assert out["recently_updated"][0]["record_id"] == "s3"
    months = {m["month"]: m for m in out["timeline"]}
    assert len(out["timeline"]) == 12 and months["2026-09"]["new_postings"] == 1 and months["2026-10"]["updates"] == 2
    assert shortage_insights([], today=today)["current"]["days_listed"]["median"] is None


def test_eval_runner_new_checks_and_junit(container: Container, tmp_path: Path) -> None:
    scen = tmp_path / "s.yaml"
    scen.write_text("""
scenarios:
  - id: renal_cg
    title: estimate
    request: { drug: atorvastatin, route: oral, patient: { age_years: 84, weight_kg: 52, sex: female, serum_creatinine: 2.4 } }
    expect:
      renal_source: cockcroft_gault
      warnings_contain: [Cockcroft-Gault]
  - id: ranking
    title: route ranking
    request: { drug: furosemide, route: intravenous, current_dose: { amount: 40, unit: mg } }
    expect:
      ranked_above: [{ higher: bumetanide, lower: torsemide }]
  - id: dup
    title: duplicate
    request: { drug: atorvastatin, route: oral, patient: { current_medications: [simvastatin] } }
    expect:
      gate_fact: [{ ingredient: rosuvastatin, gate: interactions, key: duplicate_therapy, contains: simvastatin }]
  - id: deliberately_failing
    title: must fail
    request: { drug: furosemide, route: intravenous }
    expect:
      ranked_above: [{ higher: torsemide, lower: bumetanide }]
""")
    report = run_suite(container.service, scen, tmp_path / "out")
    by = {r["id"]: r["passed"] for r in report["scenarios"]}
    assert by == {"renal_cg": True, "ranking": True, "dup": True, "deliberately_failing": False}
    assert report["summary"]["gate_outcomes"]
    xml = (tmp_path / "out" / "evaluation.junit.xml").read_text()
    assert 'tests="4" failures="1"' in xml and "<failure" in xml
    assert junit_xml([]).count("<testcase") == 0


def test_http_cache_ttl(tmp_path: Path, settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"n": calls["n"]})

    client = HttpClient(settings, cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    assert client.get_json("https://x.test/a", max_age_s=60)["n"] == 1
    assert client.get_json("https://x.test/a", max_age_s=60)["n"] == 1  # fresh cache hit
    path = next(tmp_path.glob("*.json"))
    old = time.time() - 120
    os.utime(path, (old, old))
    assert client.get_json("https://x.test/a", max_age_s=60)["n"] == 2  # expired, refetched
    path.write_text("{torn")
    assert client.get_json("https://x.test/a", max_age_s=None)["n"] == 3  # corrupt entry discarded
    assert json.loads(path.read_text())["n"] == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Furosemide 10 mg/mL injection", ["furosemide"]), ("Amoxicillin 400 mg/5 mL oral suspension", ["amoxicillin"]),
     ("Heparin 5,000 units/mL vial", ["heparin"]), ("morphine sulfate 30 mg ER tablet", ["morphine"])],
)
def test_names_with_strengths_resolve(text: str, expected: list[str]) -> None:
    assert ingredients_from_shortage_name(text) == expected


def test_clinician_text_with_strength_resolves(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(drug="Furosemide 40 mg tablet", route="oral"), audit=False)
    assert rec.query and rec.query.ingredients == ["furosemide"]


def test_plural_word_boundary() -> None:
    assert word_boundary_contains("allergic to sulfonamides", "sulfonamide", plural=True)
    assert not word_boundary_contains("allergic to sulfonamides", "sulfonamide")
    assert not word_boundary_contains("sulfonamidesx", "sulfonamide", plural=True)
