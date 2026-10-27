from pathlib import Path

from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import SectionType
from shortfall_bridge.domain.models import PatientContext
from shortfall_bridge.embeddings import HashingEmbedder
from shortfall_bridge.ingestion.chunking import chunk_section
from shortfall_bridge.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from shortfall_bridge.retrieval.intent import Intent, detect_intents, section_priors
from shortfall_bridge.store.memory import MemoryStore


def test_rrf_rewards_agreement() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]], k=60)
    assert fused["a"] == fused["b"] > fused["c"] > 0
    assert max(fused, key=fused.get) in {"a", "b"}


def test_chunker_respects_budget_and_overlap() -> None:
    text = " ".join(f"Sentence number {i} describes renal dosing for clinicians." for i in range(80))
    chunks = chunk_section(text, target_tokens=60, overlap_tokens=15)
    assert len(chunks) > 5
    assert all(len(c.split()) * 1.3 <= 60 + 15 for c in chunks)
    assert chunks[0].split(".")[-2].strip() in chunks[1]  # overlap carries the last sentence forward


def test_chunker_keeps_subheadings_with_content() -> None:
    text = "8.6 Renal Impairment Reduce the dose when CrCl is less than 30 mL/min. 8.7 Hepatic Impairment No change."
    chunks = chunk_section(text, target_tokens=200)
    assert "8.6 Renal Impairment" in chunks[0]


def test_intents_from_context() -> None:
    p = PatientContext(creatinine_clearance_ml_min=25, current_medications=["warfarin"], pregnant=True)
    intents = detect_intents(None, p)
    assert {Intent.RENAL, Intent.INTERACTIONS, Intent.PREGNANCY} <= set(intents)
    priors = section_priors(intents)
    assert priors[SectionType.CONTRAINDICATIONS] >= priors[SectionType.PREGNANCY]


def test_ingredient_filter_and_dedup(store: MemoryStore, settings: Settings) -> None:
    r = HybridRetriever(store, HashingEmbedder(settings.embedding_dim), settings)
    hits = r.retrieve("severe renal impairment dose", k=5, ingredients=["rosuvastatin"])
    assert hits and all("rosuvastatin" in h.chunk.ingredients[0] for h in hits)
    texts = [h.chunk.text for h in hits]
    assert len(texts) == len(set(texts))


def test_section_priors_change_order(store: MemoryStore, settings: Settings) -> None:
    r = HybridRetriever(store, HashingEmbedder(settings.embedding_dim), settings)
    priors = section_priors([Intent.INTERACTIONS])
    hits = r.retrieve("clarithromycin", k=3, ingredients=["simvastatin"], priors=priors)
    assert hits[0].chunk.section in {SectionType.CONTRAINDICATIONS, SectionType.DRUG_INTERACTIONS}


def test_snapshot_round_trip(store: MemoryStore, tmp_path: Path) -> None:
    store.save(tmp_path)
    clone = MemoryStore(dim=store.dim)
    clone.load(tmp_path)
    assert clone.stats() == store.stats()
