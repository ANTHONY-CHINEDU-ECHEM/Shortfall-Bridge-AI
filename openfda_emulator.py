"""A small emulator of the openFDA query API over the test fixtures.

It understands the subset of openFDA query syntax the live store emits (quoted phrases, trailing
wildcards, OR by whitespace, AND, ``count`` on ``.exact`` fields, ``limit`` and ``skip``) and
returns HTTP 404 for no matches exactly as openFDA does. It is used two ways:

* as an in process ``Fetcher`` for unit tests of the live store, and
* as a local HTTP server (``python tests/openfda_emulator.py --port 8765``) for the browser end
  to end test of the Netlify demo, which is pointed at it instead of api.fda.gov.

The fixtures are miniature openFDA shaped records written for tests; they are not real labels.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent / "fixtures"
_CLAUSE = re.compile(r'([\w.]+):(?:"([^"]*)"|(\S+))')


def _field_values(rec: dict[str, Any], field: str) -> list[str]:
    node: Any = rec
    for part in field.split("."):
        if isinstance(node, list):
            node = [x.get(part) if isinstance(x, dict) else None for x in node]
            node = [v for sub in node for v in (sub if isinstance(sub, list) else [sub]) if v is not None]
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return []
        if node is None:
            return []
    vals = node if isinstance(node, list) else [node]
    return [str(v) for v in vals if v is not None]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _clause_matches(rec: dict[str, Any], field: str, phrase: str | None, bare: str | None) -> bool:
    values = _field_values(rec, field.removesuffix(".exact"))
    if phrase is not None:
        want = _tokens(phrase)
        for v in values:
            toks = _tokens(v)
            for i in range(len(toks) - len(want) + 1):
                if toks[i: i + len(want)] == want:
                    return True
        return False
    assert bare is not None
    if bare.endswith("*"):
        prefix = bare[:-1].lower()
        return any(t.startswith(prefix) for v in values for t in _tokens(v))
    return any(bare.lower() in _tokens(v) for v in values)


def matches(rec: dict[str, Any], search: str | None) -> bool:
    if not search:
        return True
    for part in re.split(r"\s+AND\s+", search.replace("+", " ")):
        clauses = _CLAUSE.findall(part)
        if clauses and not any(_clause_matches(rec, f, None if bare else ph, bare or None)
                               for f, ph, bare in clauses):
            return False
    return True


class OpenFDAEmulator:
    def __init__(self, fixtures: Path = FIXTURES) -> None:
        self.labels: list[dict[str, Any]] = json.loads((fixtures / "labels.json").read_text())
        self.shortages: list[dict[str, Any]] = json.loads((fixtures / "shortages.json").read_text())
        self.applications = self._drugsfda(fixtures / "orange_book_products.txt")
        self.calls: list[str] = []

    @staticmethod
    def _drugsfda(path: Path) -> list[dict[str, Any]]:
        apps: dict[str, dict[str, Any]] = {}
        with path.open(encoding="latin-1") as fh:
            for row in csv.DictReader(fh, delimiter="~"):
                appl = ("ANDA" if row["Appl_Type"] == "A" else "NDA") + row["Appl_No"]
                form, _, route = row["DF;Route"].partition(";")
                app = apps.setdefault(appl, {"application_number": appl, "sponsor_name": row["Applicant"],
                                             "products": []})
                app["products"].append({
                    "product_number": row["Product_No"], "brand_name": row["Trade_Name"],
                    "active_ingredients": [{"name": row["Ingredient"], "strength": row["Strength"]}],
                    "dosage_form": form, "route": route, "te_code": row["TE_Code"] or None,
                    "reference_drug": row["RLD"], "reference_standard": row["RS"],
                    "marketing_status": {"RX": "Prescription", "OTC": "Over-the-counter"}.get(row["Type"], "Discontinued"),
                })
        return list(apps.values())

    def query(self, path: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.calls.append(f"{path}?{params.get('search', '')}|{params.get('count', '')}")
        corpus = {"/drug/label.json": self.labels, "/drug/shortages.json": self.shortages,
                  "/drug/drugsfda.json": self.applications}.get(path)
        if corpus is None:
            return 404, {"error": {"code": "NOT_FOUND", "message": "No such endpoint"}}
        hits = [r for r in corpus if matches(r, params.get("search"))]
        if not hits:
            return 404, {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
        limit = int(params.get("limit", "1"))
        if params.get("count"):
            counts: Counter[str] = Counter()
            for r in hits:
                for v in set(_field_values(r, params["count"].removesuffix(".exact"))):
                    counts[v] += 1
            return 200, {"meta": {"results": {"total": len(counts)}},
                         "results": [{"term": t, "count": c} for t, c in counts.most_common(limit)]}
        skip = int(params.get("skip", "0"))
        page = hits[skip: skip + limit]
        if not page:
            return 404, {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
        return 200, {"meta": {"results": {"skip": skip, "limit": limit, "total": len(hits)}}, "results": page}


class EmulatorFetcher:
    """``Fetcher`` over the emulator, mirroring HttpClient's 404 to empty result mapping."""

    def __init__(self, emulator: OpenFDAEmulator | None = None, fail_kinds: Sequence[str] = ()) -> None:
        self.emu = emulator or OpenFDAEmulator()
        self.fail_kinds = tuple(fail_kinds)

        from shortfall_bridge.sources.openfda_live import FetchLog

        self.log = FetchLog()
        self.kinds: list[str] = []
        self.batches: list[int] = []

    def get_json(self, req: Any) -> dict[str, Any]:
        self.log.requests += 1
        self.kinds.append(req.kind)
        if any(req.kind.startswith(k) for k in self.fail_kinds):
            return {"results": [], "_error": f"{req.kind}: simulated outage"}
        status, body = self.emu.query(req.path, dict(req.params))
        return body if status == 200 else {"results": [], "_not_found": True}

    def get_many(self, reqs: Sequence[Any]) -> list[dict[str, Any]]:
        self.batches.append(len(reqs))
        return [self.get_json(r) for r in reqs]


def serve(port: int, fixtures: Path = FIXTURES, delay_ms: int = 0) -> None:
    emu = OpenFDAEmulator(fixtures)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            import time

            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path.removeprefix("/api/fda")
            status, body = emu.query(path, params)
            if delay_ms:
                time.sleep(delay_ms / 1000)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:  # keep test output quiet
            return

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Serve the openFDA emulator over HTTP")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--delay-ms", type=int, default=0)
    a = ap.parse_args()
    print(f"openFDA emulator on http://127.0.0.1:{a.port}")
    serve(a.port, delay_ms=a.delay_ms)

