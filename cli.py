"""Command line interface: ingestion, enrichment, evaluation and serving.

Examples
    shortfall ingest-all                       # full corpus: every openFDA label partition
    shortfall ingest-labels --max-partitions 2 # quick start subset
    shortfall enrich-classes
    shortfall stats
    shortfall recommend "furosemide injection" --route intravenous --dose 40 --crcl 45
    shortfall evaluate
    shortfall serve
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import get_settings
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
    frequency: str | None = None,
    crcl: float | None = None,
    pregnant: bool = False,
    allergy: Annotated[list[str] | None, typer.Option()] = None,
    med: Annotated[list[str] | None, typer.Option()] = None,
    question: str | None = None,
) -> None:
    """Run one substitution request and print the JSON recommendation."""
    from .api.container import Container

    settings, store = _ctx()
    c = Container.build(settings, store=store)
    req = SubstitutionRequest(
        drug=drug, route=route, dosage_form=form, strength=strength,
        current_dose=DoseInput(amount=dose, frequency=frequency) if dose else None,
        patient=PatientContext(creatinine_clearance_ml_min=crcl, pregnant=pregnant,
                               allergies=allergy or [], current_medications=med or []),
        clinical_question=question,
    )
    typer.echo(c.service.recommend(req).model_dump_json(indent=2))


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
