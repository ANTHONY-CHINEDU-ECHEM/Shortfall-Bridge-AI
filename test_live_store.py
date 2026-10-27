"""Live openFDA store: parity with the indexed store, request budget, lazy fallback and failure handling."""

from datetime import UTC, datetime, timedelta

from shortfall_bridge.api.container import Container
from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import CandidateTier, GateName, GateStatus, RecommendationStatus
from shortfall_bridge.domain.models import DoseInput, PatientContext, SubstitutionRequest
from shortfall_bridge.embeddings import HashingEmbedder
from shortfall_bridge.sources.openfda_live import (
    class_members_request,
    label_request,
    members_from_count,
    parse_drugsfda,
    search_request,
)
from shortfall_bridge.store.live import LiveOpenFDAStore

from .openfda_emulator import EmulatorFetcher, OpenFDAEmulator


def _live(settings: Settings, fetcher: EmulatorFetcher | None = None) -> tuple[Container, LiveOpenFDAStore, EmulatorFetcher]:
    f = fetcher or EmulatorFetcher()
    s = settings.model_copy(update={"store_backend": "live"})
    store = LiveOpenFDAStore(f, HashingEmbedder(s.embedding_dim), s)
    return Container.build(s, store=store, embedder=HashingEmbedder(s.embedding_dim), online_resolution=False), store, f


FURO = SubstitutionRequest(drug="Furosemide Injection", route="intravenous",
                           current_dose=DoseInput(amount=40, frequency="twice daily"),
                           patient=PatientContext(creatinine_clearance_ml_min=42, current_medications=["lisinopril"]))


def _summary(rec):
    return [(c.tier, c.ingredient, c.dose_conversion.suggested_dose if c.dose_conversion else None,
             tuple(g.status for g in c.gates)) for c in rec.candidates], sorted(c.ingredient for c in rec.not_recommended)


def test_query_builders_are_lucene_safe() -> None:
    r = label_request('methylprednisolone "sodium" (succinate)', 5, None)
    assert dict(r.params)["search"] == 'openfda.substance_name:"methylprednisolone sodium succinate"'
    assert "+" not in dict(search_request("furo", None).params)["search"]  # spaces, never literal plus
    assert dict(search_request("morphine sulfate", None).params)["search"].count('"morphine sulfate"') == 3
    assert search_request("x", None) is None
    c = class_members_request("Loop Diuretic [EPC]", "parenteral", None)
    assert dict(c.params)["search"] == 'openfda.pharm_class_epc:"loop diuretic" AND openfda.route:"intravenous"'
    assert dict(c.params)["count"] == "openfda.substance_name.exact"


def test_parsers() -> None:
    resp = {"results": [{"term": "FUROSEMIDE", "count": 10}, {"term": "BUMETANIDE", "count": 4},
                        {"term": "METHYLPREDNISOLONE SODIUM SUCCINATE", "count": 3}]}
    assert members_from_count(resp, {"furosemide"}, 5) == ["bumetanide", "methylprednisolone"]
    products = parse_drugsfda(OpenFDAEmulator().query("/drug/drugsfda.json",
                                                      {"search": 'products.active_ingredients.name:"furosemide"',
                                                       "limit": "100"})[1])
    hosp = next(p for p in products if p.applicant == "HOSPIRA")
    assert hosp.is_marketed and hosp.is_a_rated and hosp.appl_type == "A" and hosp.ingredients == ["furosemide"]


def test_live_store_matches_indexed_store(settings: Settings, container: Container) -> None:
    live, _, _ = _live(settings)
    assert _summary(live.service.recommend(FURO, audit=False)) == _summary(container.service.recommend(FURO, audit=False))
    statin = SubstitutionRequest(drug="atorvastatin", route="oral", patient=PatientContext(current_medications=["clarithromycin"]))
    a, b = live.service.recommend(statin, audit=False), container.service.recommend(statin, audit=False)
    assert _summary(a) == _summary(b) and "simvastatin" in _summary(a)[1]


def test_prefetch_plan_bounds_round_trips(settings: Settings) -> None:
    c, _, f = _live(settings)
    rec = c.service.recommend(FURO, audit=False)
    assert rec.status in (RecommendationStatus.OK, RecommendationStatus.PARTIAL)
    assert len(f.batches) <= 4 and f.log.requests <= 16
    assert "prefetch" in rec.timings_ms and rec.data_versions["mode"] == "live_openfda"
    before = f.log.requests
    c.service.recommend(FURO, audit=False)  # everything is hydrated now
    assert f.log.requests == before


def test_lazy_hydration_without_prefetch(settings: Settings) -> None:
    _, store, f = _live(settings)
    assert store.search_ingredients("bum")[0][0] == "bumetanide"
    assert store.labels_for_ingredient("bumetanide")
    assert any(c.class_type == "EPC" for c in store.classes_for("bumetanide"))
    members = store.ingredients_in_class(store.classes_for("bumetanide")[0].class_id)
    assert {"furosemide", "torsemide"} <= set(members)
    assert store.orange_book_for_ingredient("furosemide")
    assert store.shortages_for_ingredient("furosemide")[0].status.value == "Current"
    n = f.log.requests
    store.labels_for_ingredient("bumetanide")
    assert f.log.requests == n  # cached


def test_unknown_drug_abstains_cleanly(settings: Settings) -> None:
    c, _, _ = _live(settings)
    rec = c.service.recommend(SubstitutionRequest(drug="zzqx unknownmab"), audit=False)
    assert rec.status is RecommendationStatus.ABSTAIN


def test_outage_degrades_to_partial_with_warning(settings: Settings) -> None:
    c, store, _ = _live(settings, EmulatorFetcher(fail_kinds=["drugsfda:"]))
    rec = c.service.recommend(FURO, audit=False)
    assert rec.status is RecommendationStatus.PARTIAL
    assert any("live openFDA request(s) failed (drugsfda" in w for w in rec.warnings)
    assert all(c.tier is not CandidateTier.THERAPEUTIC_EQUIVALENT or c.products for c in rec.candidates)
    assert store.readiness()[1]["recent_errors"]


def test_shortage_ttl_refresh(settings: Settings) -> None:
    _, store, f = _live(settings)
    store.shortages()
    n = f.log.requests
    store.shortages()
    assert f.log.requests == n
    store._shortages_loaded_at = datetime.now(UTC) - timedelta(seconds=settings.live_shortage_ttl_s + 5)
    store.shortages()
    assert f.log.requests > n


def test_combination_labels_do_not_leak_into_classes(settings: Settings) -> None:
    emu = OpenFDAEmulator()
    emu.labels.append({
        "set_id": "combo", "id": "combo-v", "version": "1", "effective_time": "20250101",
        "openfda": {"substance_name": ["LISINOPRIL", "BUMETANIDE"], "generic_name": ["LISINOPRIL AND BUMETANIDE"],
                    "pharm_class_epc": ["Loop Diuretic [EPC]"], "route": ["ORAL"], "product_type": ["HUMAN PRESCRIPTION DRUG"]},
        "indications_and_usage": ["Combination for testing."],
    })
    _, store, _ = _live(settings, EmulatorFetcher(emu))
    cid = store.classes_for("furosemide")[0].class_id
    assert "lisinopril" not in store.ingredients_in_class(cid)


def test_ready_and_meta_in_live_mode(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from shortfall_bridge.api.app import create_app

    c, _, _ = _live(settings)
    with TestClient(create_app(c.settings, c)) as client:
        r = client.get("/ready")
        assert r.status_code == 200 and r.json()["mode"] == "live_openfda"
        assert client.get("/api/v1/meta").json()["store_mode"] == "live_openfda"
        body = client.get("/api/v1/drugs/search", params={"q": "furo"}).json()
        assert body[0]["ingredient"] == "furosemide" and body[0]["in_current_shortage"] is True


def test_live_interaction_block_parity(settings: Settings) -> None:
    c, _, _ = _live(settings)
    rec = c.service.recommend(SubstitutionRequest(drug="atorvastatin", route="oral",
                                                  patient=PatientContext(current_medications=["clarithromycin"])),
                              audit=False)
    sim = next(x for x in rec.not_recommended if x.ingredient == "simvastatin")
    g = next(g for g in sim.gates if g.gate is GateName.INTERACTIONS)
    assert g.status is GateStatus.BLOCK and g.citations


def test_live_store_over_real_http(settings: Settings, tmp_path) -> None:
    """build_store('live') wiring: httpx client, retries, TTL disk cache, thread pool and 404 handling."""
    import socket
    import threading

    from shortfall_bridge.store.base import build_store

    from .openfda_emulator import serve

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    s = settings.model_copy(update={"store_backend": "live", "openfda_base_url": f"http://127.0.0.1:{port}",
                                    "data_dir": tmp_path})
    import time

    for _ in range(50):  # wait for the server socket
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    store = build_store(s, HashingEmbedder(s.embedding_dim))
    c = Container.build(s, store=store, embedder=HashingEmbedder(s.embedding_dim), online_resolution=False)
    rec = c.service.recommend(FURO, audit=False)
    assert any(x.ingredient == "bumetanide" for x in rec.candidates)
    assert store.fetcher.log.requests > 0 and not store.fetcher.log.errors
    assert list((tmp_path / "cache" / "openfda_live").glob("*.json"))  # responses cached on disk
