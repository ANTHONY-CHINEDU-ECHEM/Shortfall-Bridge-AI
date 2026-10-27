"""openFDA clients: structured product labels (bulk) and the FDA Drug Shortages database.

Labels
    openFDA publishes every SPL on DailyMed as JSON partitions listed in the download manifest
    (``https://api.fda.gov/download.json`` -> results.drug.label.partitions). Each partition is a
    zip containing one JSON document with a ``results`` array. Sections arrive pre split into
    fields such as ``contraindications`` and ``drug_interactions``, and an ``openfda`` block carries
    harmonised identifiers (RxCUI, UNII, substance names, EPC classes).

Shortages
    ``/drug/shortages.json`` exposes presentation level shortage entries maintained by FDA CDER.
    The dataset is small enough to page through the query API with ``limit`` and ``skip``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..config import Settings
from ..domain.enums import OPENFDA_FIELD_TO_SECTION, SectionType, ShortageStatus
from ..domain.models import LabelDocument, ShortageRecord
from ..domain.normalize import (
    active_moiety,
    ingredients_from_shortage_name,
    normalize_ingredient,
    normalize_route,
    split_ingredients,
)
from ..logging_setup import log_event
from .dates import parse_date
from .http import HttpClient

logger = logging.getLogger(__name__)


# Manifest and bulk labels ---------------------------------------------------------------------


def fetch_manifest(client: HttpClient, settings: Settings) -> dict[str, Any]:
    return client.get_json(settings.openfda_manifest_url, use_cache=False)


def partition_urls(manifest: dict[str, Any], category: str, endpoint: str) -> list[str]:
    node = manifest.get("results", {}).get(category, {}).get(endpoint, {})
    return [p["file"] for p in node.get("partitions", []) if p.get("file")]


def download_label_partitions(
    client: HttpClient, settings: Settings, dest_dir: Path, max_partitions: int | None = None
) -> list[Path]:
    manifest = fetch_manifest(client, settings)
    urls = partition_urls(manifest, "drug", "label")
    if not urls:
        raise RuntimeError("openFDA manifest did not list drug label partitions")
    if max_partitions:
        urls = urls[:max_partitions]
    paths: list[Path] = []
    for url in urls:
        path, sha = client.download(url, dest_dir / Path(url).name)
        log_event(logger, "label_partition_ready", file=path.name, sha256=sha)
        paths.append(path)
    return paths


def iter_label_json(zip_path: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            with zf.open(name) as fh:
                doc = json.load(fh)
            yield from doc.get("results", [])


def _first(values: Any) -> str | None:
    if isinstance(values, list):
        return str(values[0]) if values else None
    return str(values) if values else None


def _join(values: Any) -> str:
    if isinstance(values, list):
        return "\n".join(str(v) for v in values if v)
    return str(values or "")


def parse_label_record(rec: dict[str, Any]) -> tuple[LabelDocument, dict[SectionType, str]] | None:
    """Convert one openFDA label record into a LabelDocument and its section texts.

    Records without any ingredient identity (no openfda block and no generic name) are rejected,
    since they cannot be linked to shortages, the Orange Book or classes.
    """
    of = rec.get("openfda") or {}
    set_id = rec.get("set_id") or _first(of.get("spl_set_id"))
    if not set_id:
        return None

    substance_names = of.get("substance_name") or []
    generic_names = [g for g in (of.get("generic_name") or []) if g]
    ingredients: list[str] = []
    for s in substance_names:
        n = normalize_ingredient(s)
        if n and n not in ingredients:
            ingredients.append(n)
    if not ingredients:
        for g in generic_names:
            for n in split_ingredients(g):
                if n not in ingredients:
                    ingredients.append(n)
    if not ingredients:
        return None

    brand = [b for b in (of.get("brand_name") or []) if b]
    title_bits = brand[:1] or generic_names[:1] or [ingredients[0]]
    manufacturer = _first(of.get("manufacturer_name"))
    title = f"{title_bits[0].title()}" + (f" ({manufacturer})" if manufacturer else "")

    doc = LabelDocument(
        set_id=set_id,
        spl_id=rec.get("id"),
        version=str(rec.get("version")) if rec.get("version") is not None else None,
        effective_time=parse_date(rec.get("effective_time")),
        title=title,
        generic_names=[g.lower() for g in generic_names],
        brand_names=brand,
        ingredients=ingredients,
        manufacturer=manufacturer,
        routes=[r for r in (normalize_route(x) for x in of.get("route") or []) if r],
        product_type=_first(of.get("product_type")),
        rxcuis=[str(x) for x in of.get("rxcui") or []],
        pharm_class_epc=list(of.get("pharm_class_epc") or []),
        pharm_class_moa=list(of.get("pharm_class_moa") or []),
        application_numbers=list(of.get("application_number") or []),
        source="openfda_label",
    )

    sections: dict[SectionType, str] = {}
    for field, section in OPENFDA_FIELD_TO_SECTION.items():
        text = _join(rec.get(field)).strip()
        if text:
            # nursing_mothers and lactation can both exist; keep the longer text.
            if section in sections and len(sections[section]) >= len(text):
                continue
            sections[section] = text
    if not sections:
        return None
    return doc, sections


# Shortages ------------------------------------------------------------------------------------


def iter_shortages_api(client: HttpClient, settings: Settings, page_size: int = 1000) -> Iterator[dict[str, Any]]:
    url = f"{settings.openfda_base_url}/drug/shortages.json"
    skip = 0
    total: int | None = None
    while total is None or skip < total:
        params: dict[str, Any] = {"limit": page_size, "skip": skip}
        if settings.openfda_api_key:
            params["api_key"] = settings.openfda_api_key
        data = client.get_json(url, params=params, use_cache=False)
        results = data.get("results", [])
        if total is None:
            total = int(data.get("meta", {}).get("results", {}).get("total", len(results)))
            log_event(logger, "shortage_api_total", total=total)
        if not results:
            break
        yield from results
        skip += len(results)


def parse_shortage_record(rec: dict[str, Any]) -> ShortageRecord | None:
    generic = (rec.get("generic_name") or "").strip()
    if not generic:
        return None
    of = rec.get("openfda") or {}
    ingredients = [active_moiety(s) for s in of.get("substance_name") or [] if s]
    if not ingredients:
        ingredients = ingredients_from_shortage_name(generic)
    key = "|".join(
        str(rec.get(k) or "") for k in ("generic_name", "company_name", "presentation", "package_ndc", "dosage_form")
    )
    record_id = "s_" + hashlib.sha1(key.encode()).hexdigest()[:16]
    strength = rec.get("strength") or []
    if isinstance(strength, str):
        strength = [strength]
    category = rec.get("therapeutic_category") or []
    if isinstance(category, str):
        category = [category]
    return ShortageRecord(
        record_id=record_id,
        generic_name=generic,
        ingredients=sorted(set(ingredients)),
        proprietary_name=rec.get("proprietary_name"),
        company_name=rec.get("company_name"),
        presentation=rec.get("presentation"),
        dosage_form=rec.get("dosage_form"),
        strength=[str(s) for s in strength],
        status=ShortageStatus.parse(rec.get("status")),
        availability=rec.get("availability"),
        shortage_reason=rec.get("shortage_reason"),
        related_info=rec.get("related_info"),
        therapeutic_category=[str(c) for c in category],
        package_ndc=rec.get("package_ndc"),
        routes=[r for r in (normalize_route(x) for x in of.get("route") or []) if r],
        rxcuis=[str(x) for x in of.get("rxcui") or []],
        pharm_class_epc=list(of.get("pharm_class_epc") or []),
        initial_posting_date=parse_date(rec.get("initial_posting_date")),
        update_date=parse_date(rec.get("update_date")),
    )
