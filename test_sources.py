import json
import zipfile
from pathlib import Path

import httpx

from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import SectionType, ShortageStatus
from shortfall_bridge.sources.dailymed import parse_spl_xml
from shortfall_bridge.sources.http import HttpClient
from shortfall_bridge.sources.openfda import (
    iter_label_json,
    iter_shortages_api,
    parse_label_record,
    parse_shortage_record,
    partition_urls,
)
from shortfall_bridge.sources.orange_book import iter_products

FIX = Path(__file__).parent / "fixtures"


def test_parse_label_record(label_records: list[dict]) -> None:
    doc, sections = parse_label_record(label_records[0])
    assert doc.set_id == "fx-iv-hospira"
    assert doc.ingredients == ["furosemide"]
    assert doc.routes == ["intravenous"]
    assert SectionType.CONTRAINDICATIONS in sections and SectionType.BOXED_WARNING in sections
    assert doc.effective_time.year == 2024


def test_label_without_identity_is_rejected() -> None:
    assert parse_label_record({"set_id": "x", "contraindications": ["None."]}) is None


def test_iter_label_json_reads_partition_zip(tmp_path: Path, label_records: list[dict]) -> None:
    z = tmp_path / "drug-label-0001-of-0001.json.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("drug-label-0001-of-0001.json", json.dumps({"meta": {}, "results": label_records[:3]}))
    assert len(list(iter_label_json(z))) == 3


def test_manifest_partition_urls() -> None:
    manifest = {"results": {"drug": {"label": {"partitions": [{"file": "https://x/1.zip"}, {"file": "https://x/2.zip"}]}}}}
    assert partition_urls(manifest, "drug", "label") == ["https://x/1.zip", "https://x/2.zip"]


def test_parse_shortage_record() -> None:
    raw = json.loads((FIX / "shortages.json").read_text())
    r = parse_shortage_record(raw[0])
    assert r.status is ShortageStatus.CURRENT and r.ingredients == ["furosemide"]
    assert r.update_date.isoformat() == "2026-09-12"
    r2 = parse_shortage_record(raw[2])
    assert r2.ingredients == ["amoxicillin"]


def test_shortage_api_pagination() -> None:
    pages = {0: [{"generic_name": "A"}, {"generic_name": "B"}], 2: [{"generic_name": "C"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params["skip"])
        return httpx.Response(200, json={"meta": {"results": {"total": 3}}, "results": pages.get(skip, [])})

    s = Settings(log_json=False)
    client = HttpClient(s, transport=httpx.MockTransport(handler))
    names = [r["generic_name"] for r in iter_shortages_api(client, s, page_size=2)]
    assert names == ["A", "B", "C"]


def test_http_404_is_empty_result() -> None:
    client = HttpClient(Settings(), transport=httpx.MockTransport(lambda r: httpx.Response(404, json={})))
    assert client.get_json("https://api.fda.gov/drug/shortages.json", use_cache=False)["results"] == []


def test_orange_book_parsing() -> None:
    prods = list(iter_products(FIX / "orange_book_products.txt"))
    fx = [p for p in prods if p.ingredient == "FUROSEMIDE" and p.route == "INJECTION"]
    assert len(fx) == 3 and all(p.is_a_rated and p.is_marketed for p in fx)
    assert prods[0].ingredients == ["furosemide"]


SPL = b"""<?xml version="1.0"?>
<document xmlns="urn:hl7-org:v3">
  <id root="spl-1"/><setId root="set-123"/><versionNumber value="4"/><effectiveTime value="20240115"/>
  <title>BUMETANIDE injection</title>
  <author><assignedEntity><representedOrganization><name>Example Labeler</name></representedOrganization></assignedEntity></author>
  <component><structuredBody>
    <component><section><code code="34067-9"/><text><paragraph>Bumetanide is indicated for edema.</paragraph></text></section></component>
    <component><section><code code="34070-3"/><text><paragraph>Contraindicated in anuria.</paragraph></text></section></component>
    <component><section><code code="48780-1"/><subject><manufacturedProduct><manufacturedProduct><name>Bumetanide</name>
      <ingredient classCode="ACTIM"><ingredientSubstance><name>BUMETANIDE</name></ingredientSubstance></ingredient>
    </manufacturedProduct><consumedIn><substanceAdministration><routeCode displayName="INTRAVENOUS"/></substanceAdministration></consumedIn></manufacturedProduct></subject></section></component>
  </structuredBody></component>
</document>"""


def test_parse_spl_xml() -> None:
    doc, sections = parse_spl_xml(SPL)
    assert doc.set_id == "set-123" and doc.ingredients == ["bumetanide"]
    assert doc.routes == ["intravenous"]
    assert sections[SectionType.CONTRAINDICATIONS] == "Contraindicated in anuria."
