"""v2 API surface: security, new endpoints, and parity between FastAPI and the browser dispatcher."""

import json

import pytest
from fastapi.testclient import TestClient

from shortfall_bridge.api.app import SlidingWindowLimiter, create_app
from shortfall_bridge.api.container import Container
from shortfall_bridge.api.dispatch import dispatch
from shortfall_bridge.api.handlers import ApiHandlers
from shortfall_bridge.config import Settings

FURO = {"drug": "furosemide", "route": "intravenous", "current_dose": {"amount": 40, "unit": "mg", "frequency": "twice daily"},
        "patient": {"creatinine_clearance_ml_min": 40}}


@pytest.fixture(scope="module")
def client(settings: Settings, container: Container):
    with TestClient(create_app(settings, container)) as c:
        yield c


def test_security_headers(client: TestClient) -> None:
    r = client.get("/api/v1/meta")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy",
              "X-Request-ID", "X-RateLimit-Remaining"):
        assert h in r.headers
    assert r.headers["Cache-Control"] == "no-store"
    assert "Content-Security-Policy" not in client.get("/docs").headers  # Swagger UI needs its scripts


def test_meta_reports_features(client: TestClient) -> None:
    m = client.get("/api/v1/meta").json()
    assert m["store_mode"] == "memory" and m["runtime"] == "server" and m["auth_required"] is False
    assert {"cds_hooks_2", "renal_estimation", "hash_chained_audit"} <= set(m["features"])


def test_api_key_authentication(settings: Settings, container: Container) -> None:
    secured = settings.model_copy(update={"api_keys": "k-one, k-two"})
    with TestClient(create_app(secured, container)) as c:
        assert c.get("/health").status_code == 200 and c.get("/ready").status_code == 200
        r = c.get("/api/v1/meta")
        assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
        assert c.get("/api/v1/meta", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/api/v1/meta", headers={"X-API-Key": "k-two"}).json()["auth_required"] is True
        assert c.get("/api/v1/meta", headers={"Authorization": "Bearer k-one"}).status_code == 200
        assert c.get("/cds-services").status_code == 401
        assert c.options("/api/v1/meta", headers={"Origin": "http://localhost:5173",
                                                  "Access-Control-Request-Method": "GET"}).status_code == 200


def test_body_size_limit(settings: Settings, container: Container) -> None:
    small = settings.model_copy(update={"max_body_bytes": 200})
    with TestClient(create_app(small, container)) as c:
        r = c.post("/api/v1/substitutions", content=json.dumps({**FURO, "clinical_question": "x" * 400}),
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 413


def test_rate_limit_uses_forwarded_client_when_trusted(settings: Settings, container: Container) -> None:
    tight = settings.model_copy(update={"api_rate_limit_per_minute": 2, "trust_proxy_headers": True})
    with TestClient(create_app(tight, container)) as c:
        a = {"X-Forwarded-For": "10.0.0.1"}
        assert [c.get("/api/v1/meta", headers=a).status_code for _ in range(3)] == [200, 200, 429]
        assert c.get("/api/v1/meta", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200
    untrusted = settings.model_copy(update={"api_rate_limit_per_minute": 2, "trust_proxy_headers": False})
    with TestClient(create_app(untrusted, container)) as c:
        codes = [c.get("/api/v1/meta", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 429]  # spoofed headers are ignored without a trusted proxy


def test_limiter_prunes_idle_clients() -> None:
    lim = SlidingWindowLimiter(5, prune_every=3)
    for i in range(3):
        lim.allow(f"c{i}")
    for q in lim.hits.values():
        q[0] -= 120  # age every entry past the window
    lim.allow("fresh")
    lim.allow("fresh")
    lim.allow("fresh")
    assert set(lim.hits) == {"fresh"}


def test_batch_endpoint(client: TestClient) -> None:
    body = {"requests": [FURO, {"drug": "atorvastatin", "route": "oral", "patient": {"current_medications": ["clarithromycin"]}},
                         {"drug": "zzqx unknownmab"}], "labels": ["Bed 1", "Bed 2", "Bed 3"]}
    r = client.post("/api/v1/substitutions/batch", json=body)
    assert r.status_code == 200
    out = r.json()
    assert [x["label"] for x in out["results"]] == ["Bed 1", "Bed 2", "Bed 3"]
    s = out["summary"]
    assert s["requests"] == 3 and s["by_status"].get("abstain") == 1 and s["in_shortage"] == 1
    assert s["escalate"][0]["label"] == "Bed 3" and "simvastatin" in s["blocked_somewhere"]
    assert client.post("/api/v1/substitutions/batch", json={"requests": []}).status_code == 422
    assert client.post("/api/v1/substitutions/batch", json={"requests": [FURO] * 26}).status_code == 422


def test_insights_endpoint(client: TestClient) -> None:
    out = client.get("/api/v1/shortages/insights").json()
    assert out["total_records"] == 3 and out["by_status"] == {"Current": 2, "Resolved": 1}
    cur = out["current"]
    assert cur["entries"] == 2 and {"name": "parenteral", "count": 1} in cur["by_route_family"]
    assert {"name": "oral", "count": 1} in cur["by_route_family"]
    assert cur["by_reason"][0] == {"name": "Demand increase for the drug", "count": 2}
    assert len(out["timeline"]) == 24 and cur["longest_running"]


def test_note_review_and_audit_verify(client: TestClient) -> None:
    rid = client.post("/api/v1/substitutions", json=FURO).json()["request_id"]
    note = client.get(f"/api/v1/audit/{rid}/note")
    assert note.status_code == 200 and note.headers["content-type"].startswith("text/markdown")
    assert f'shortfall-{rid}.md' in note.headers["content-disposition"]
    text = note.text
    assert text.startswith("# Shortage substitution review: furosemide") and "## Options reviewed, ranked" in text
    assert "Audit record" in text and "hash" in text and "Reviewed by:" in text
    plain = client.get(f"/api/v1/audit/{rid}/note", params={"format": "text"}).text
    assert plain.startswith("SHORTAGE SUBSTITUTION REVIEW") and "**" not in plain
    assert client.get(f"/api/v1/audit/{rid}/note", params={"format": "pdf"}).status_code == 422
    assert client.get("/api/v1/audit/r_nope/note").status_code == 404

    ok = client.post("/api/v1/reviews", json={"request_id": rid, "candidate_ingredient": "bumetanide", "decision": "agree"})
    assert ok.status_code == 201 and ok.json()["rank"] >= 1 and ok.json()["review_id"].startswith("rv_")
    assert client.post("/api/v1/reviews", json={"request_id": rid, "candidate_ingredient": "bumetanide",
                                                "decision": "override"}).status_code == 422
    assert client.post("/api/v1/reviews", json={"request_id": rid, "candidate_ingredient": "warfarin",
                                                "decision": "agree"}).status_code == 422
    assert client.post("/api/v1/reviews", json={"request_id": "r_missing", "candidate_ingredient": "bumetanide",
                                                "decision": "agree"}).status_code == 404
    client.post("/api/v1/reviews", json={"request_id": rid, "candidate_ingredient": "torsemide", "decision": "reject",
                                         "reason": "Needs IV route"})
    assert len(client.get("/api/v1/reviews", params={"request_id": rid}).json()) == 2
    summary = client.get("/api/v1/reviews/summary").json()
    assert summary["reviews"] >= 2 and summary["top_override_reasons"][0]["reason"] == "needs iv route"
    v = client.get("/api/v1/audit/verify").json()
    assert v["ok"] is True and v["checked"] >= 1 and len(v["head_hash"]) == 64


def test_equivalence_and_renal_endpoints(client: TestClient) -> None:
    tables = client.get("/api/v1/equivalence/tables").json()
    assert {t["id"] for t in tables} >= {"opioid_mme", "loop_diuretics", "statin_intensity"}
    r = client.post("/api/v1/equivalence/convert", json={"from_drug": "furosemide", "to_drug": "bumetanide",
                                                         "dose": {"amount": 40}, "from_route": "intravenous",
                                                         "to_route": "intravenous"})
    assert r.status_code == 200 and r.json()["suggested_dose"] == "2 mg"
    assert client.post("/api/v1/equivalence/convert", json={"from_drug": "furosemide", "to_drug": "warfarin",
                                                            "dose": {"amount": 40}}).status_code == 404
    e = client.post("/api/v1/renal/estimate", json={"age_years": 72, "weight_kg": 80, "height_cm": 175, "sex": "male",
                                                    "serum_creatinine": 150, "serum_creatinine_unit": "umol/L"}).json()
    assert e["source"] == "cockcroft_gault" and abs(e["value_ml_min"] - 39.2) < 0.2


def test_response_carries_v2_fields(client: TestClient) -> None:
    rec = client.post("/api/v1/substitutions", json=FURO).json()
    assert rec["engine_version"] and rec["timings_ms"]["gates"] >= 0 and rec["renal"]["source"] == "entered_crcl"
    c = rec["candidates"][0]
    assert c["score_breakdown"][0]["term"] == "tier" and "routes_available" in c
    assert all("facts" in g for g in c["gates"])


REQUESTS = [
    ("GET", "/api/v1/meta", None),
    ("GET", "/api/v1/drugs/search?q=furo", None),
    ("GET", "/api/v1/drugs/search?q=f", None),
    ("GET", "/api/v1/shortages?status=Current&limit=5", None),
    ("GET", "/api/v1/shortages?status=Nonsense", None),
    ("GET", "/api/v1/equivalence/tables", None),
    ("POST", "/api/v1/equivalence/convert", {"from_drug": "oxycodone", "to_drug": "morphine", "dose": {"amount": 10}}),
    ("POST", "/api/v1/renal/estimate", {"age_years": 50, "sex": "male", "serum_creatinine": 1.0}),
    ("POST", "/api/v1/substitutions", {"drug": "x"}),
    ("POST", "/api/v1/substitutions", {"drug": "furosemide", "patient": {"creatinine_clearance_ml_min": -1}}),
    ("GET", "/api/v1/evidence/c_missing", None),
    ("GET", "/api/v1/audit/r_missing", None),
    ("GET", "/cds-services", None),
    ("POST", "/cds-services/nope", {}),
    ("GET", "/api/v1/nothing", None),
]


@pytest.mark.parametrize(("method", "url", "body"), REQUESTS)
def test_browser_dispatcher_matches_fastapi(client: TestClient, container: Container, method: str, url: str,
                                            body: dict | None) -> None:
    http = client.request(method, url, json=body)
    local = dispatch(ApiHandlers(container), method, url, body)
    assert local.status == http.status_code
    if http.status_code == 200:
        envelope = json.loads(local.to_json())["body"]
        expected = http.json()
        if url == "/api/v1/meta":
            expected.pop("runtime"), envelope.pop("runtime")
        assert envelope == expected
    elif http.status_code == 422 and isinstance(http.json()["detail"], list):
        assert [d["loc"][-1] for d in json.loads(local.to_json())["body"]["detail"]] == \
               [d["loc"][-1] for d in http.json()["detail"]]


def test_dispatcher_recommendation_and_text_payloads(container: Container) -> None:
    h = ApiHandlers(container)
    rec = json.loads(dispatch(h, "POST", "/api/v1/substitutions", json.dumps(FURO)).to_json())
    assert rec["status"] == 200 and rec["body"]["candidates"]
    note = json.loads(dispatch(h, "GET", f"/api/v1/audit/{rec['body']['request_id']}/note?format=text").to_json())
    assert note["content_type"] == "text/plain" and note["body"].startswith("SHORTAGE SUBSTITUTION REVIEW")
    assert dispatch(h, "POST", "/api/v1/substitutions", "{not json").status == 422
    assert dispatch(h, "DELETE", "/api/v1/meta").status == 405
