from shortfall_bridge.api.container import Container
from shortfall_bridge.domain.enums import CandidateTier, GateName, GateStatus, RecommendationStatus
from shortfall_bridge.domain.models import DoseInput, PatientContext, SubstitutionRequest


def _gate(c, name: GateName):
    return next(g for g in c.gates if g.gate is name)


def test_furosemide_iv_end_to_end(container: Container) -> None:
    rec = container.service.recommend(
        SubstitutionRequest(
            drug="Furosemide Injection", route="intravenous", current_dose=DoseInput(amount=40, frequency="twice daily"),
            patient=PatientContext(creatinine_clearance_ml_min=42, current_medications=["lisinopril"]),
        )
    )
    assert rec.status in (RecommendationStatus.OK, RecommendationStatus.PARTIAL)
    assert rec.shortage and rec.shortage.in_shortage
    tiers = {(c.tier, c.ingredient) for c in rec.candidates}
    # Hospira is the affected manufacturer; Fresenius and Baxter remain A rated sources
    t1 = next(c for c in rec.candidates if c.tier is CandidateTier.THERAPEUTIC_EQUIVALENT)
    assert all("HOSPIRA" not in p.upper() for p in t1.products)
    assert (CandidateTier.THERAPEUTIC_INTERCHANGE, "bumetanide") in tiers
    bum = next(c for c in rec.candidates if c.ingredient == "bumetanide")
    assert bum.dose_conversion and bum.dose_conversion.suggested_dose.startswith("2 mg")
    oral = [c for c in rec.candidates if c.tier is CandidateTier.ALTERNATIVE_FORMULATION]
    assert oral and any(c.dose_conversion and c.dose_conversion.suggested_dose.startswith("80 mg") for c in oral)
    # every candidate carries all eight gates
    assert all(len(c.gates) == 8 for c in rec.candidates)
    # citations resolve and point at DailyMed
    for cid, cit in rec.citations.items():
        assert cid == cit.chunk_id and cit.dailymed_url.startswith("https://dailymed.nlm.nih.gov/")
    assert rec.narrative.sentences and rec.narrative.generator == "template"


def test_interaction_blocks_simvastatin(container: Container) -> None:
    rec = container.service.recommend(
        SubstitutionRequest(drug="atorvastatin", route="oral", current_dose=DoseInput(amount=20),
                            patient=PatientContext(current_medications=["clarithromycin"]))
    )
    blocked = {c.ingredient: c for c in rec.not_recommended}
    assert "simvastatin" in blocked
    g = _gate(blocked["simvastatin"], GateName.INTERACTIONS)
    assert g.status is GateStatus.BLOCK and g.citations
    assert all(c.ingredient != "simvastatin" for c in rec.candidates)


def test_renal_gate_unknown_without_crcl_then_caution(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(drug="atorvastatin", route="oral"))
    ros = next(c for c in rec.candidates if c.ingredient == "rosuvastatin")
    assert _gate(ros, GateName.RENAL).status is GateStatus.UNKNOWN
    rec2 = container.service.recommend(
        SubstitutionRequest(drug="atorvastatin", route="oral", patient=PatientContext(creatinine_clearance_ml_min=20))
    )
    ros2 = next(c for c in rec2.candidates if c.ingredient == "rosuvastatin")
    assert _gate(ros2, GateName.RENAL).status is GateStatus.CAUTION


def test_pregnancy_surfaces_fetal_risk(container: Container) -> None:
    rec = container.service.recommend(
        SubstitutionRequest(drug="atorvastatin", route="oral", patient=PatientContext(pregnant=True))
    )
    ros = next(c for c in rec.candidates + rec.not_recommended if c.ingredient == "rosuvastatin")
    assert _gate(ros, GateName.PREGNANCY_LACTATION).status in (GateStatus.CAUTION, GateStatus.BLOCK)


def test_allergy_to_ingredient_blocks(container: Container) -> None:
    rec = container.service.recommend(
        SubstitutionRequest(drug="furosemide", route="intravenous", patient=PatientContext(allergies=["bumetanide"]))
    )
    assert any(c.ingredient == "bumetanide" for c in rec.not_recommended)


def test_methadone_conversion_refused(container: Container) -> None:
    rec = container.service.recommend(
        SubstitutionRequest(drug="morphine sulfate", route="oral", current_dose=DoseInput(amount=30))
    )
    meth = [c for c in rec.candidates + rec.not_recommended if c.ingredient == "methadone"]
    assert meth and meth[0].dose_conversion and not meth[0].dose_conversion.feasible
    assert meth[0].dose_conversion.method.startswith("refused")


def test_unknown_drug_abstains(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(drug="zzqx unknownmab"))
    assert rec.status is RecommendationStatus.ABSTAIN and rec.abstain_reasons


def test_audit_record_written(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(drug="dexamethasone", route="intravenous"))
    assert container.store.get_audit(rec.request_id)["response"]["request_id"] == rec.request_id


def test_scores_are_monotonic_with_tier(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(drug="furosemide", route="intravenous"))
    scores = [c.score for c in rec.candidates]
    assert scores == sorted(scores, reverse=True)
