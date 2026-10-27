"""openFDA query API access for the live knowledge store.

The live store answers requests from the public openFDA query API instead of a pre built index.
This module holds everything that is specific to that API so it can be shared by the server
(httpx, thread pool) and the in browser runtime (Pyodide, fetch):

* request builders that return plain ``FetchRequest`` values (no I/O),
* parsers that turn responses into domain records (Drugs@FDA products to Orange Book shaped
  products, count responses to ingredient lists),
* the ``Fetcher`` protocol with a server implementation.

Endpoints used, all documented at https://open.fda.gov/apis/drug/
    /drug/label.json        structured product labels, searched by substance or class
    /drug/shortages.json    FDA Drug Shortages entries (the whole table is a few pages)
    /drug/drugsfda.json     Drugs@FDA applications and products, including TE codes

openFDA returns HTTP 404 for a query with no matches; fetchers map that to an empty result.
Clauses are joined with real spaces (OR) and " AND ": HTTP clients percent encode a literal "+",
which Lucene would then read as a "must" operator and silently change the query.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain.models import OrangeBookProduct
from ..domain.normalize import active_moiety, split_ingredients

logger = logging.getLogger(__name__)

SHORTAGE_PAGE = 1000  # openFDA maximum page size

# openFDA route values used to scope class member searches to the requested route family.
FAMILY_TO_OPENFDA_ROUTE = {
    "parenteral": "INTRAVENOUS",
    "oral": "ORAL",
    "inhalation": "RESPIRATORY (INHALATION)",
    "topical": "TOPICAL",
    "ophthalmic": "OPHTHALMIC",
    "transdermal": "TRANSDERMAL",
    "nasal": "NASAL",
    "rectal": "RECTAL",
    "oromucosal": "SUBLINGUAL",
}


@dataclass(frozen=True)
class FetchRequest:
    """One GET against openFDA. ``ttl_s`` is how long a cached response may be reused."""

    path: str
    params: tuple[tuple[str, str], ...]
    ttl_s: float | None = None
    kind: str = ""

    def url(self, base: str) -> str:
        return f"{base.rstrip('/')}{self.path}"

    def as_params(self, api_key: str | None = None) -> dict[str, str]:
        out = dict(self.params)
        if api_key:
            out["api_key"] = api_key
        return out


class Fetcher(Protocol):
    def get_json(self, req: FetchRequest) -> dict[str, Any]: ...

    def get_many(self, reqs: Sequence[FetchRequest]) -> list[dict[str, Any]]: ...


@dataclass
class FetchLog:
    requests: int = 0
    errors: list[str] = field(default_factory=list)


# Query builders --------------------------------------------------------------------------------

_SAFE = re.compile(r"[^a-z0-9 \-]")


def _term(text: str) -> str:
    """Lucene safe search term: lower case letters, digits, spaces and hyphens only."""
    return " ".join(_SAFE.sub(" ", text.lower()).split())


def label_request(ingredient: str, limit: int, ttl_s: float | None) -> FetchRequest:
    t = _term(ingredient)
    return FetchRequest("/drug/label.json", (("search", f'openfda.substance_name:"{t}"'), ("limit", str(limit))),
                        ttl_s, kind=f"labels:{t}")


def drugsfda_request(ingredient: str, ttl_s: float | None) -> FetchRequest:
    t = _term(ingredient)
    return FetchRequest("/drug/drugsfda.json",
                        (("search", f'products.active_ingredients.name:"{t}"'), ("limit", "100")),
                        ttl_s, kind=f"drugsfda:{t}")


def shortage_page_request(skip: int, ttl_s: float | None) -> FetchRequest:
    return FetchRequest("/drug/shortages.json", (("limit", str(SHORTAGE_PAGE)), ("skip", str(skip))),
                        ttl_s, kind=f"shortages:{skip}")


def search_request(text: str, ttl_s: float | None, limit: int = 10) -> FetchRequest | None:
    t = _term(text)
    if len(t) < 2:
        return None
    if " " in t:
        clause = " ".join(f'openfda.{f}:"{t}"' for f in ("substance_name", "generic_name", "brand_name"))
    else:
        clause = " ".join(f"openfda.{f}:{t}*" for f in ("substance_name", "generic_name", "brand_name"))
    return FetchRequest("/drug/label.json",
                        (("search", clause), ("count", "openfda.substance_name.exact"), ("limit", str(limit))),
                        ttl_s, kind=f"search:{t}")


def class_members_request(epc_class: str, family: str | None, ttl_s: float | None, limit: int = 40) -> FetchRequest:
    name = re.sub(r"\s*\[(?:EPC|MoA|PE|CS)\]\s*$", "", epc_class).strip()
    clause = f'openfda.pharm_class_epc:"{_term(name)}"'
    route = FAMILY_TO_OPENFDA_ROUTE.get(family or "")
    if route:
        clause += f' AND openfda.route:"{_term(route)}"'
    return FetchRequest("/drug/label.json",
                        (("search", clause), ("count", "openfda.substance_name.exact"), ("limit", str(limit))),
                        ttl_s, kind=f"class:{_term(name)}:{family or 'any'}")


# Parsers ----------------------------------------------------------------------------------------


def count_terms(resp: dict[str, Any]) -> list[tuple[str, int]]:
    return [(str(r.get("term", "")), int(r.get("count", 0))) for r in resp.get("results") or [] if r.get("term")]


def members_from_count(resp: dict[str, Any], exclude: set[str], limit: int) -> list[str]:
    """Distinct active moieties from a count response, most labelled first."""
    out: list[str] = []
    for term, _ in count_terms(resp):
        m = active_moiety(term)
        if m and m not in exclude and m not in out:
            out.append(m)
        if len(out) >= limit:
            break
    return out


_MARKETING = {"prescription": "RX", "over-the-counter": "OTC", "discontinued": "DISCN"}


def parse_drugsfda(resp: dict[str, Any]) -> list[OrangeBookProduct]:
    """Drugs@FDA application records to Orange Book shaped products (one per marketed product)."""
    out: list[OrangeBookProduct] = []
    for app in resp.get("results") or []:
        appl = str(app.get("application_number") or "")
        prefix = next((p for p in ("ANDA", "NDA", "BLA") if appl.upper().startswith(p)), "")
        appl_type = {"ANDA": "A", "NDA": "N", "BLA": "B"}.get(prefix, "")
        sponsor = str(app.get("sponsor_name") or "").strip()
        for prod in app.get("products") or []:
            ais = prod.get("active_ingredients") or []
            names = [str(a.get("name") or "") for a in ais if a.get("name")]
            if not names:
                continue
            ingredient = "; ".join(names)
            status = str(prod.get("marketing_status") or "").strip().lower()
            out.append(OrangeBookProduct(
                ingredient=ingredient,
                ingredients=[active_moiety(i) for n in names for i in split_ingredients(n)],
                dosage_form=str(prod.get("dosage_form") or ""),
                route=str(prod.get("route") or ""),
                trade_name=str(prod.get("brand_name") or ingredient),
                applicant=sponsor,
                applicant_full_name=sponsor or None,
                strength="; ".join(str(a.get("strength") or "") for a in ais),
                appl_type=appl_type,
                appl_no=appl[len(prefix):],
                product_no=str(prod.get("product_number") or ""),
                te_code=(str(prod.get("te_code")).strip() or None) if prod.get("te_code") else None,
                approval_date=None,
                rld=str(prod.get("reference_drug") or "").lower() == "yes",
                rs=str(prod.get("reference_standard") or "").lower() == "yes",
                marketing_type=_MARKETING.get(status, "OTHER"),
            ))
    return out


def total_of(resp: dict[str, Any]) -> int:
    return int(((resp.get("meta") or {}).get("results") or {}).get("total") or len(resp.get("results") or []))


# Server fetcher ----------------------------------------------------------------------------------


class HttpFetcher:
    """Server side fetcher over the resilient HttpClient (retries, rate limit, TTL disk cache)."""

    def __init__(self, client: Any, base_url: str, api_key: str | None = None, max_workers: int = 6) -> None:
        self.client, self.base, self.api_key, self.max_workers = client, base_url, api_key, max_workers
        self.log = FetchLog()

    def get_json(self, req: FetchRequest) -> dict[str, Any]:
        self.log.requests += 1
        try:
            return self.client.get_json(req.url(self.base), req.as_params(self.api_key),
                                        use_cache=req.ttl_s is not None and req.ttl_s > 0, max_age_s=req.ttl_s)
        except Exception as exc:  # one failed query degrades that source, it never fails the request
            msg = f"{req.kind}: {str(exc)[:160]}"
            self.log.errors.append(msg)
            logger.warning("openfda_live_fetch_failed %s", msg)
            return {"results": [], "_error": msg}

    def get_many(self, reqs: Sequence[FetchRequest]) -> list[dict[str, Any]]:
        if len(reqs) <= 1:
            return [self.get_json(r) for r in reqs]
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(reqs))) as pool:
            return list(pool.map(self.get_json, reqs))


class StaticFetcher:
    """Answers from a dictionary keyed by request kind. Used by tests and offline replays."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.log = FetchLog()
        self.seen: list[str] = []

    def get_json(self, req: FetchRequest) -> dict[str, Any]:
        self.log.requests += 1
        self.seen.append(req.kind)
        return self.responses.get(req.kind, {"results": [], "_not_found": True})

    def get_many(self, reqs: Sequence[FetchRequest]) -> list[dict[str, Any]]:
        return [self.get_json(r) for r in reqs]
