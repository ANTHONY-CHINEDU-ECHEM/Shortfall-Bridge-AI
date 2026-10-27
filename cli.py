"""Command line interface: ingestion, enrichment, evaluation and serving.

Examples
    shortfall ingest-all                       # full corpus: every openFDA label partition
    shortfall ingest-labels --max-partitions 2 # quick start subset
    shortfall enrich-classes
    shortfall stats
    shortfall recommend "furosemide injection" --route intravenous --dose 40 --crcl 45
    shortfall evaluate
    shortfall serve

v2 additions
    SFB_STORE_BACKEND=live shortfall recommend furosemide --route intravenous   # no ingestion needed
    shortfall renal --age 72 --weight 80 --height 175 --sex male --scr 150 --scr-unit umol/L
    shortfall convert furosemide bumetanide 40 --from-route intravenous --to-route intravenous
    shortfall batch ward_requests.json
    shortfall fhir medication_request_bundle.json
    shortfall insights
    shortfall audit-verify
    shortfall note r_0123456789abcdef0123
    shortfall openapi --out openapi.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import get_settings
from .domain.enums import CreatinineUnit, HepaticImpairment, Sex
from .domain.models import DoseInput, PatientContext, SubstitutionRequest
from .embeddings import build_embedder
from .ingestion import pipeline
from .logging_setup import configure_logging
from .sources.http import HttpClient
from .sources.rxnav import RxNavClient
from .store.base import KnowledgeStore, build_store

app = typer.Typer(add_completion=False, help="Shortfall Bridge AI command line interface")


def _ctx() -> tuple:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = build_store(settings)
    return settings, store


def _persist(settings, store: KnowledgeStore) -> None:
    if settings.store_backend == "memory":
        path = settings.snapshot_path or settings.data_dir / "snapshot"
        store.save(path)  # type: ignore[attr-defined]
        typer.echo(f"memory snapshot written to {path}")


@app.command("ingest-labels")
def ingest_labels(
    max_partitions: Annotated[int | None, typer.Option(help="Limit openFDA label partitions (None = all)")] = None,
    max_records: Annotated[int | None, typer.Option(help="Stop after N label records")] = None,
    local: Annotated[list[Path] | None, typer.Option(help="Use already downloaded partition zips")] = None,
) -> None:
    """Download and index openFDA structured product labels."""
    settings, store = _ctx()
    embedder = build_embedder(settings)
    with HttpClient(settings) as client:
        stats = pipeline.ingest_label_partitions(
            store, embedder, settings, client, max_partitions, max_records, list(local) if local else None
        )
    _persist(settings, store)
    typer.echo(json.dumps(stats.__dict__, indent=2))


@app.command("ingest-dailymed")
def ingest_dailymed(
    zip_paths: Annotated[list[Path], typer.Argument(help="DailyMed bulk release zips (dm_spl_release_*.zip)")],
    max_records: int | None = None,
) -> None:
    """Index DailyMed SPL XML bulk releases (alternative or supplement to openFDA labels)."""
    settings, store = _ctx()
    stats = pipeline.ingest_dailymed_bulk(store, build_embedder(settings), settings, zip_paths, max_records)
    _persist(settings, store)
    typer.echo(json.dumps(stats.__dict__, indent=2))


@app.command("ingest-shortages")
def ingest_shortages() -> None:
    """Refresh the FDA Drug Shortages table from openFDA."""
    settings, store = _ctx()
    with HttpClient(settings) as client:
        n = pipeline.ingest_shortages(store, settings, client)
    _persist(settings, store)
    typer.echo(f"{n} shortage records")


@app.command("ingest-orange-book")
def ingest_orange_book(local: Annotated[Path | None, typer.Option(help="Local Orange Book zip")] = None) -> None:
    """Load Orange Book products with therapeutic equivalence codes."""
    settings, store = _ctx()
    with HttpClient(settings) as client:
        n = pipeline.ingest_orange_book(store, settings, client, local)
    _persist(settings, store)
    typer.echo(f"{n} Orange Book products")


@app.command("enrich-classes")
def enrich_classes(limit: int | None = None) -> None:
    """Attach ATC and EPC classes from NLM RxClass to every indexed ingredient."""
    settings, store = _ctx()
    with HttpClient(settings, settings.data_dir / "cache" / "rxnav", settings.rxnav_requests_per_second) as http:
        n = pipeline.enrich_classes(store, RxNavClient(http, settings), limit=limit)
    _persist(settings, store)
    typer.echo(f"{n} class links")


@app.command("ingest-all")
def ingest_all(max_partitions: int | None = None, class_limit: int | None = None) -> None:
    """Run every ingestion job in dependency order."""
    settings, store = _ctx()
    embedder = build_embedder(settings)
    with HttpClient(settings) as client:
        pipeline.ingest_label_partitions(store, embedder, settings, client, max_partitions)
        pipeline.ingest_shortages(store, settings, client)
        pipeline.ingest_orange_book(store, settings, client)
    with HttpClient(settings, settings.data_dir / "cache" / "rxnav", settings.rxnav_requests_per_second) as http:
        pipeline.enrich_classes(store, RxNavClient(http, settings), limit=class_limit)
    _persist(settings, store)
    typer.echo(json.dumps(store.stats(), indent=2))


@app.command()
def stats() -> None:
    """Print corpus statistics and data freshness."""
    _, store = _ctx()
    typer.echo(json.dumps({"corpus": store.stats(), "data_versions": store.data_versions()}, indent=2))


@app.command()
def recommend(
    drug: str,
    route: str | None = None,
    form: str | None = None,
    strength: str | None = None,
    dose: float | None = None,
    unit: str = "mg",
    frequency: str | None = None,
    crcl: float | None = None,
    egfr: float | None = None,
    age: float | None = None,
    weight: float | None = None,
    height: float | None = None,
    sex: Sex = Sex.UNKNOWN,
    scr: Annotated[float | None, typer.Option(help="Serum creatinine")] = None,
    scr_unit: CreatinineUnit = CreatinineUnit.MG_DL,
    dialysis: bool = False,
    hepatic: HepaticImpairment = HepaticImpairment.NONE,
    pregnant: bool = False,
    breastfeeding: bool = False,
    allergy: Annotated[list[str] | None, typer.Option()] = None,
    med: Annotated[list[str] | None, typer.Option()] = None,
    question: str | None = None,
    no_alt_class: bool = False,
) -> None:
    """Run one substitution request and print the JSON recommendation."""
    from .api.container import Container

    settings, store = _ctx()
    c = Container.build(settings, store=store)
    req = SubstitutionRequest(
        drug=drug, route=route, dosage_form=form, strength=strength,
        current_dose=DoseInput(amount=dose, unit=unit, frequency=frequency) if dose else None,
        patient=PatientContext(
            creatinine_clearance_ml_min=crcl, egfr_ml_min_1_73m2=egfr, age_years=age, weight_kg=weight,
            height_cm=height, sex=sex, serum_creatinine=scr, serum_creatinine_unit=scr_unit, on_dialysis=dialysis,
            hepatic_impairment=hepatic, pregnant=pregnant, breastfeeding=breastfeeding,
            allergies=allergy or [], current_medications=med or [],
        ),
        clinical_question=question, include_alternative_classes=not no_alt_class,
    )
    typer.echo(c.service.recommend(req).model_dump_json(indent=2))


@app.command()
def renal(
    age: Annotated[float, typer.Option(help="Age in years")], weight: float | None = None, height: float | None = None, sex: Sex = Sex.UNKNOWN,
    scr: float | None = None, scr_unit: CreatinineUnit = CreatinineUnit.MG_DL, dialysis: bool = False,
) -> None:
    """Estimate renal function (Cockcroft-Gault, CKD-EPI 2021 or bedside Schwartz) as the engine would."""
    from .clinical.renal import estimate_renal_function

    p = PatientContext(age_years=age, weight_kg=weight, height_cm=height, sex=sex, serum_creatinine=scr,
                       serum_creatinine_unit=scr_unit, on_dialysis=dialysis)
    typer.echo(estimate_renal_function(p).model_dump_json(indent=2))


@app.command()
def convert(
    from_drug: str, to_drug: str, dose: float, unit: str = "mg", frequency: str | None = None,
    from_route: str | None = None, to_route: str | None = None,
) -> None:
    """Convert a dose with the published equivalence tables (no knowledge base needed)."""
    from .engine.equivalence import convert as do_convert

    dc = do_convert(from_drug, to_drug, DoseInput(amount=dose, unit=unit, frequency=frequency), from_route, to_route)
    if dc is None:
        typer.echo(f"No published equivalence table relates {from_drug} and {to_drug}.", err=True)
        raise typer.Exit(code=2)
    typer.echo(dc.model_dump_json(indent=2))


def _handlers():
    from .api.container import Container
    from .api.handlers import ApiHandlers

    settings, store = _ctx()
    return ApiHandlers(Container.build(settings, store=store, online_resolution=settings.store_backend != "live"))


@app.command()
def batch(path: Path) -> None:
    """Evaluate a JSON file of requests (a list, or {"requests": [...], "labels": [...]})."""
    from .api.schemas import BatchRequest

    raw = json.loads(path.read_text())
    body = BatchRequest.model_validate({"requests": raw} if isinstance(raw, list) else raw)
    out = _handlers().batch(body)
    typer.echo(json.dumps(out.summary, indent=2, default=str))


@app.command()
def fhir(path: Path, order_id: str | None = None) -> None:
    """Map a FHIR R4 Bundle or MedicationRequest and run the substitution."""
    out = _handlers().fhir_substitution(json.loads(path.read_text()), order_id)
    typer.echo(out.model_dump_json(indent=2))


@app.command()
def insights() -> None:
    """Shortage landscape analytics (categories, reasons, durations, timeline)."""
    typer.echo(json.dumps(_handlers().insights(), indent=2, default=str))


@app.command("audit-verify")
def audit_verify() -> None:
    """Verify the hash chained audit log. Exits non zero when tampering is detected."""
    report = _handlers().audit_verify()
    typer.echo(report.model_dump_json(indent=2))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def note(request_id: str, format: str = "markdown") -> None:
    """Render the clinical note for a stored recommendation."""
    typer.echo(_handlers().note(request_id, format))


@app.command()
def openapi(out: Path = Path("openapi.json")) -> None:
    """Write the OpenAPI schema without starting the server."""
    from .api.app import create_app

    out.write_text(json.dumps(create_app().openapi(), indent=2))
    typer.echo(f"OpenAPI schema written to {out}")


@app.command()
def evaluate(
    scenarios: Annotated[Path | None, typer.Option(help="Scenario YAML")] = None,
    out_dir: Path = Path("reports"),
) -> None:
    """Run the clinical scenario evaluation suite and write a report."""
    from .api.container import Container
    from .evaluation.runner import run_suite

    settings, store = _ctx()
    c = Container.build(settings, store=store, online_resolution=False)
    report = run_suite(c.service, scenarios, out_dir)
    typer.echo(json.dumps(report["summary"], indent=2))


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, workers: int = 1) -> None:
    """Serve the API with uvicorn."""
    import uvicorn

    uvicorn.run("shortfall_bridge.api.app:app_factory", factory=True, host=host, port=port, workers=workers)


if __name__ == "__main__":
    app()
