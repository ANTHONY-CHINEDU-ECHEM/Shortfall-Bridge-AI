import pytest
from fastapi.testclient import TestClient

from shortfall_bridge.api.app import create_app
from shortfall_bridge.api.container import Container
from shortfall_bridge.config import Settings


@pytest.fixture(scope="module")
def client(settings: Settings, container: Container):
    with TestClient(create_app(settings, container)) as c:
        yield c


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["corpus"]["labels"] > 0


def test_meta(client: TestClient) -> None:
    m = client.get("/api/v1/meta").json()
    assert len(m["gates"]) == 8 and len(m["tiers"]) == 4


def test_drug_search_flags_shortage(client: TestClient) -> None:
    r = client.get("/api/v1/drugs/search", params={"q": "furo"}).json()
    assert r[0]["ingredient"] == "furosemide" and r[0]["in_current_shortage"] is True


def test_shortage_listing_filters(client: TestClient) -> None:
    r = client.get("/api/v1/shortages", params={"status": "Current"}).json()
    assert r["total"] == 2 and all(i["status"] == "Current" for i in r["items"])


def test_substitution_round_trip_and_audit(client: TestClient) -> None:
    body = {"drug": "furosemide", "route": "intravenous", "current_dose": {"amount": 40, "unit": "mg"},
            "patient": {"creatinine_clearance_ml_min": 40}}
    r = client.post("/api/v1/substitutions", json=body)
    assert r.status_code == 200 and r.headers["X-Request-ID"]
    data = r.json()
    assert data["candidates"]
    cid = next(iter(data["citations"]))
    ev = client.get(f"/api/v1/evidence/{cid}").json()
    assert ev["citation"]["chunk_id"] == cid and ev["label"]["set_id"]
    audit = client.get(f"/api/v1/audit/{data['request_id']}")
    assert audit.status_code == 200


def test_validation_errors(client: TestClient) -> None:
    assert client.post("/api/v1/substitutions", json={"drug": "x"}).status_code == 422
    bad = {"drug": "furosemide", "patient": {"creatinine_clearance_ml_min": -5}}
    assert client.post("/api/v1/substitutions", json=bad).status_code == 422


def test_metrics_exposed(client: TestClient) -> None:
    text = client.get("/metrics").text
    assert "sfb_recommendations_total" in text and "sfb_gate_outcomes_total" in text
