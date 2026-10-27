"""DailyMed SPL XML parser and web service client.

openFDA bulk JSON is the primary label corpus because it is pre sectioned and harmonised. The
DailyMed path exists for two reasons: refreshing an individual label between openFDA releases
(DailyMed updates daily) and auditing a citation against the authoritative XML. Sections are
identified by LOINC codes, so the parser is independent of heading wording.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

from lxml import etree

from ..config import Settings
from ..domain.enums import LOINC_TO_SECTION, SectionType
from ..domain.models import LabelDocument
from ..domain.normalize import normalize_ingredient, normalize_route
from .dates import parse_date
from .http import HttpClient

logger = logging.getLogger(__name__)

NS = {"v3": "urn:hl7-org:v3"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=True)


def _text_of(node: etree._Element) -> str:
    """Flatten a section to readable text while keeping paragraph and list item boundaries."""
    parts: list[str] = []
    for el in node.iter():
        tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
        if tag in {"paragraph", "item", "caption", "td", "th", "title"}:
            text = " ".join("".join(el.itertext()).split())
            if text:
                parts.append(text)
    if not parts:
        parts.append(" ".join("".join(node.itertext()).split()))
    deduped: list[str] = []
    for p in parts:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return "\n".join(deduped)


def parse_spl_xml(xml_bytes: bytes) -> tuple[LabelDocument, dict[SectionType, str]] | None:
    root = etree.fromstring(xml_bytes, parser=_PARSER)
    if root is None:
        return None
    set_id_el = root.find("v3:setId", NS)
    set_id = set_id_el.get("root") if set_id_el is not None else None
    if not set_id:
        return None
    spl_id_el = root.find("v3:id", NS)
    version_el = root.find("v3:versionNumber", NS)
    eff_el = root.find("v3:effectiveTime", NS)
    title = " ".join("".join(root.find("v3:title", NS).itertext()).split()) if root.find("v3:title", NS) is not None else ""

    ingredients: list[str] = []
    for ing in root.iterfind(".//v3:ingredient", NS):
        if (ing.get("classCode") or "").upper() not in {"ACTIB", "ACTIM", "ACTIR"}:
            continue
        name_el = ing.find("v3:ingredientSubstance/v3:name", NS)
        if name_el is not None and name_el.text:
            n = normalize_ingredient(name_el.text)
            if n not in ingredients:
                ingredients.append(n)

    routes: list[str] = []
    for r in root.iterfind(".//v3:routeCode", NS):
        name = normalize_route(r.get("displayName"))
        if name and name not in routes:
            routes.append(name)

    manufacturer_el = root.find(".//v3:author//v3:representedOrganization/v3:name", NS)
    brand_el = root.find(".//v3:manufacturedProduct/v3:manufacturedProduct/v3:name", NS)

    sections: dict[SectionType, str] = {}
    for sec in root.iterfind(".//v3:section", NS):
        code_el = sec.find("v3:code", NS)
        code = code_el.get("code") if code_el is not None else None
        section = LOINC_TO_SECTION.get(code or "")
        if not section:
            continue
        text = _text_of(sec)
        if text and len(text) > len(sections.get(section, "")):
            sections[section] = text

    if not ingredients or not sections:
        return None
    manufacturer = manufacturer_el.text.strip() if manufacturer_el is not None and manufacturer_el.text else None
    brand = brand_el.text.strip() if brand_el is not None and brand_el.text else None
    doc = LabelDocument(
        set_id=set_id,
        spl_id=spl_id_el.get("root") if spl_id_el is not None else None,
        version=version_el.get("value") if version_el is not None else None,
        effective_time=parse_date(eff_el.get("value") if eff_el is not None else None),
        title=(brand or title[:80] or ingredients[0]).title() + (f" ({manufacturer})" if manufacturer else ""),
        brand_names=[brand] if brand else [],
        generic_names=[],
        ingredients=ingredients,
        manufacturer=manufacturer,
        routes=routes,
        source="dailymed_spl",
    )
    return doc, sections


def fetch_spl(client: HttpClient, settings: Settings, set_id: str) -> tuple[LabelDocument, dict[SectionType, str]] | None:
    """Fetch the current SPL for a set id from the DailyMed v2 web service."""
    url = f"{settings.dailymed_base_url}/spls/{set_id}.xml"
    client.limiter.wait()
    resp = client._client.get(url)
    resp.raise_for_status()
    return parse_spl_xml(resp.content)


def iter_spl_bulk(zip_path: Path) -> Iterator[bytes]:
    """DailyMed bulk releases are zips of per label zips, each holding one XML file."""
    with zipfile.ZipFile(zip_path) as outer:
        for name in outer.namelist():
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                    for inner_name in inner.namelist():
                        if inner_name.lower().endswith(".xml"):
                            yield inner.read(inner_name)
            elif name.lower().endswith(".xml"):
                yield outer.read(name)
