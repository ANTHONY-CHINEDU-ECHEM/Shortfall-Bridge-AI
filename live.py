"""Live openFDA knowledge store: the full engine with no ingestion step.

``LiveOpenFDAStore`` implements the ``KnowledgeStore`` port by fetching from the openFDA query
API on demand and indexing what it fetched into an in memory store (BM25 plus vectors), so the
engine, gates, retrieval and narrative run unchanged. It makes the system usable in minutes
instead of after a multi hour bulk ingestion, and it powers the in browser demo.

Hydration is lazy and per ingredient. The first read that needs an ingredient's labels, classes,
products or sections fetches them once; later reads are local. Class membership comes from a
count query over labels in the same EPC class (scoped to the requested route family), and every
member is then verified against its own single ingredient labels exactly as the memory store does,
so combination products cannot leak a wrong ingredient into a class.

``prefetch_plan`` is a generator that yields batches of independent requests and receives their
responses. The server drives it with a thread pool and the browser drives it with parallel
``fetch`` calls, so one plan definition serves both and a recommendation needs three or four
round trips instead of twenty sequential ones. Anything the plan missed is still fetched lazily.

Trade offs, stated in every response's ``data_versions``: only the most recent labels per
ingredient are indexed (``live_label_limit``), Orange Book TE codes come from Drugs@FDA, ATC
classes from RxClass are not used, and Tier 4 only sees indications of ingredients already
hydrated for the request.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Generator, Sequence
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..domain.enums import SectionType, ShortageStatus
from ..domain.models import (
    Chunk,
    DrugClass,
    LabelDocument,
    OrangeBookProduct,
    ShortageRecord,
    SubstitutionRequest,
)
from ..domain.normalize import active_moiety, clean_text, ingredients_from_shortage_name
from ..embeddings import Embedder
from ..engine.equivalence import interchange_partners
from ..ingestion.chunking import chunk_label
from ..sources import openfda
from ..sources.openfda_live import (
    SHORTAGE_PAGE,
    Fetcher,
    FetchRequest,
    class_members_request,
    count_terms,
    drugsfda_request,
    label_request,
    members_from_count,
    parse_drugsfda,
    search_request,
    shortage_page_request,
    total_of,
)
from .memory import MemoryStore

logger = logging.getLogger(__name__)

Plan = Generator[list[FetchRequest], list[dict[str, Any]], None]


class LiveOpenFDAStore(MemoryStore):
    live = True

    def __init__(self, fetcher: Fetcher, embedder: Embedder, settings: Settings) -> None:
        super().__init__(dim=embedder.dim)
        self.fetcher, self.embedder, self.settings = fetcher, embedder, settings
        self._lock = threading.RLock()
        self._labels_done: set[str] = set()
        self._products_done: set[str] = set()
        self._classes_done: set[str] = set()
        self._search_cache: dict[str, list[tuple[str, str, float]]] = {}
        self._shortages_loaded_at: datetime | None = None
        self._label_fetched_at: datetime | None = None
        self.errors: list[str] = []

    # Hydration primitives ----------------------------------------------------------------------

    def _apply_labels(self, ingredient: str, resp: dict[str, Any]) -> int:
        key = active_moiety(ingredient)
        n = 0
        if resp.get("_error"):
            self.errors.append(str(resp["_error"]))
        for rec in resp.get("results") or []:
            parsed = openfda.parse_label_record(rec)
            if parsed is None:
                continue
            doc, sections = parsed
            if doc.product_type and doc.product_type.upper() not in {"HUMAN PRESCRIPTION DRUG", "HUMAN OTC DRUG"}:
                continue
            chunks = chunk_label(doc, sections, self.settings.chunk_target_tokens, self.settings.chunk_overlap_tokens)
            if not chunks:
                continue
            texts = [f"{c.title}. {c.section.value.replace('_', ' ')}: {c.text}" for c in chunks]
            super().upsert_label(doc, chunks, self.embedder.embed_documents(texts))
            n += 1
        self._labels_done.add(key)
        self._label_fetched_at = datetime.now(UTC)
        return n

    def _apply_products(self, ingredient: str, resp: dict[str, Any]) -> None:
        key = active_moiety(ingredient)
        if resp.get("_error"):
            self.errors.append(str(resp["_error"]))
        existing = {(p.appl_no, p.product_no) for p in self.orange}
        for p in parse_drugsfda(resp):
            if (p.appl_no, p.product_no) not in existing:
                self.orange.append(p)
                existing.add((p.appl_no, p.product_no))
        self._products_done.add(key)

    def _apply_shortages(self, pages: Sequence[dict[str, Any]]) -> None:
        records: dict[str, ShortageRecord] = {}
        for page in pages:
            if page.get("_error"):
                self.errors.append(str(page["_error"]))
            for raw in page.get("results") or []:
                r = openfda.parse_shortage_record(raw)
                if r:
                    records[r.record_id] = r
        if records or self._shortages_loaded_at is None:
            self.shortage_records = list(records.values())
        self._shortages_loaded_at = datetime.now(UTC)

    def _apply_search(self, term: str, resp: dict[str, Any]) -> list[tuple[str, str, float]]:
        q = clean_text(term)
        scored: dict[str, float] = {}
        for raw, count in count_terms(resp):
            m = active_moiety(raw)
            if not m:
                continue
            if m == q or raw.lower() == q:
                s = 1.0
            elif m.startswith(q):
                s = 0.9
            elif q in m:
                s = 0.8
            else:
                s = 0.78  # matched through a brand or generic name field
            s += min(count, 500) / 50_000  # tie break by how many labels carry the substance
            scored[m] = max(scored.get(m, 0.0), s)
        out = sorted(((m, m, round(min(s, 1.0), 3)) for m, s in scored.items()), key=lambda x: (-x[2], x[0]))
        self._search_cache[q] = out
        return out

    def _needs_shortages(self) -> bool:
        if self._shortages_loaded_at is None:
            return True
        age = (datetime.now(UTC) - self._shortages_loaded_at).total_seconds()
        return age > self.settings.live_shortage_ttl_s

    # Lazy ensures (sequential, used when the plan did not cover something) ----------------------

    def _ensure_labels(self, ingredient: str) -> None:
        key = active_moiety(ingredient)
        if not key or key in self._labels_done:
            return
        with self._lock:
            if key in self._labels_done:
                return
            resp = self.fetcher.get_json(label_request(key, self.settings.live_label_limit, self.settings.live_label_ttl_s))
            self._apply_labels(key, resp)

    def _ensure_products(self, ingredient: str) -> None:
        key = active_moiety(ingredient)
        if not key or key in self._products_done:
            return
        with self._lock:
            if key not in self._products_done:
                self._apply_products(key, self.fetcher.get_json(drugsfda_request(key, self.settings.live_label_ttl_s)))

    def _ensure_shortages(self) -> None:
        if not self._needs_shortages():
            return
        with self._lock:
            if not self._needs_shortages():
                return
            ttl = self.settings.live_shortage_ttl_s
            first = self.fetcher.get_json(shortage_page_request(0, ttl))
            total = total_of(first)
            rest = [shortage_page_request(skip, ttl) for skip in range(SHORTAGE_PAGE, min(total, 25_000), SHORTAGE_PAGE)]
            self._apply_shortages([first, *self.fetcher.get_many(rest)])

    def _ensure_class_members(self, epc_class: str, family: str | None, exclude: set[str]) -> list[str]:
        key = f"{epc_class.lower()}|{family}"
        if key in self._classes_done or (family is None and any(
                k.startswith(f"{epc_class.lower()}|") for k in self._classes_done)):
            return []  # the prefetch plan already resolved this class (possibly route scoped)
        with self._lock:
            return self._fetch_class_members(epc_class, family, exclude, key)

    def _fetch_class_members(self, epc_class: str, family: str | None, exclude: set[str], key: str) -> list[str]:
        reqs = [class_members_request(epc_class, family, self.settings.live_label_ttl_s)]
        if family:
            reqs.append(class_members_request(epc_class, None, self.settings.live_label_ttl_s))
        responses = self.fetcher.get_many(reqs)
        members = self._members(responses, exclude)
        self._classes_done.add(key)
        todo = [m for m in members if m not in self._labels_done]
        if todo:
            ttl = self.settings.live_label_ttl_s
            for m, resp in zip(todo, self.fetcher.get_many(
                    [label_request(m, self.settings.live_label_limit, ttl) for m in todo]), strict=True):
                self._apply_labels(m, resp)
        return members

    def _members(self, responses: Sequence[dict[str, Any]], exclude: set[str]) -> list[str]:
        cap = self.settings.live_max_class_members
        out: list[str] = []
        for resp in responses:  # route scoped results first, then the unscoped fallback
            for m in members_from_count(resp, exclude, cap * 2):
                if m not in out:
                    out.append(m)
        return out[:cap]

    # Prefetch plan ------------------------------------------------------------------------------

    def prefetch_plan(self, req: SubstitutionRequest) -> Plan:
        """Batches of independent requests for one recommendation. Drive with ``run_plan``."""
        from ..domain.normalize import route_family

        s = self.settings
        ttl = s.live_label_ttl_s
        query = clean_text(req.drug)
        terms = list(dict.fromkeys([query, *ingredients_from_shortage_name(query)]))
        patient_terms = [active_moiety(x) for x in req.patient.current_medications + req.patient.allergies]

        # Batch 1: shortages, name resolution, patient medicines and allergens
        batch: list[FetchRequest] = []
        need_shortages = self._needs_shortages()
        if need_shortages:
            batch.append(shortage_page_request(0, s.live_shortage_ttl_s))
        search_terms = [t for t in terms if t and t not in self._search_cache]
        batch += [r for r in (search_request(t, ttl) for t in search_terms) if r is not None]
        patient_todo = [t for t in dict.fromkeys(patient_terms) if t and t not in self._labels_done]
        batch += [label_request(t, min(s.live_label_limit, 4), ttl) for t in patient_todo]
        responses = yield batch
        i = 0
        first_page = None
        if need_shortages:
            first_page, i = responses[0], 1
        valid_search_terms = [t for t in search_terms if search_request(t, ttl) is not None]
        for t in valid_search_terms:
            self._apply_search(t, responses[i])
            i += 1
        for t in patient_todo:
            self._apply_labels(t, responses[i])
            i += 1

        ing = None
        for t in terms:
            hits = self._search_cache.get(t) or []
            if hits and hits[0][2] >= 0.74:
                ing = hits[0][0]
                break

        # Batch 2: the drug itself, its products, equivalence partners and remaining shortage pages
        batch = []
        rest_pages: list[FetchRequest] = []
        if first_page is not None:
            total = total_of(first_page)
            rest_pages = [shortage_page_request(k, s.live_shortage_ttl_s)
                          for k in range(SHORTAGE_PAGE, min(total, 25_000), SHORTAGE_PAGE)]
            batch += rest_pages
        own: list[FetchRequest] = []
        partners: list[str] = []
        if ing:
            if ing not in self._labels_done:
                own.append(label_request(ing, s.live_label_limit, ttl))
            if ing not in self._products_done:
                own.append(drugsfda_request(ing, ttl))
            partners = [p for p in interchange_partners(ing) if p not in self._labels_done][: s.live_max_partners]
        batch += own + [label_request(p, s.live_label_limit, ttl) for p in partners]
        if batch:
            responses = yield batch
            pages = responses[: len(rest_pages)]
            if first_page is not None:
                self._apply_shortages([first_page, *pages])
            j = len(rest_pages)
            for r in own:
                if r.kind.startswith("labels:"):
                    self._apply_labels(ing or "", responses[j])
                else:
                    self._apply_products(ing or "", responses[j])
                j += 1
            for p in partners:
                self._apply_labels(p, responses[j])
                j += 1
        elif first_page is not None:
            self._apply_shortages([first_page])
        if not ing:
            return

        # Batch 3: same class members (route scoped and unscoped) for the drug's EPC classes
        family = route_family(req.route)
        epcs = [c.class_name for c in super().classes_for(ing) if c.class_type.upper() == "EPC"][:3]
        class_reqs: list[tuple[str, FetchRequest]] = []
        for e in epcs:
            if f"{e.lower()}|{family}" in self._classes_done:
                continue
            class_reqs.append((e, class_members_request(e, family, ttl)))
            if family:
                class_reqs.append((e, class_members_request(e, None, ttl)))
        if not class_reqs:
            return
        responses = yield [r for _, r in class_reqs]
        members: list[str] = []
        for e in epcs:
            group = [resp for (name, _), resp in zip(class_reqs, responses, strict=True) if name == e]
            if group:
                members += [m for m in self._members(group, {ing}) if m not in members]
                self._classes_done.add(f"{e.lower()}|{family}")

        # Batch 4: labels for the class members
        todo = [m for m in members if m not in self._labels_done][: s.live_max_class_members * 2]
        if todo:
            responses = yield [label_request(m, s.live_label_limit, ttl) for m in todo]
            for m, resp in zip(todo, responses, strict=True):
                self._apply_labels(m, resp)

    def run_plan(self, plan: Plan) -> int:
        """Drive a plan synchronously with the fetcher. Returns the number of requests issued."""
        n = 0
        try:
            batch = next(plan)
            while True:
                n += len(batch)
                batch = plan.send(self.fetcher.get_many(batch))
        except StopIteration:
            return n

    def prefetch_request(self, req: SubstitutionRequest) -> None:
        with self._lock:
            self.run_plan(self.prefetch_plan(req))

    # KnowledgeStore reads with hydration --------------------------------------------------------

    def search_ingredients(self, term: str, limit: int = 10) -> list[tuple[str, str, float]]:
        q = clean_text(term)
        local = super().search_ingredients(term, limit)
        if local and local[0][2] >= 0.95:
            return local
        if q not in self._search_cache:
            req = search_request(q, self.settings.live_label_ttl_s)
            if req is None:
                return local
            self._apply_search(q, self.fetcher.get_json(req))
        merged: dict[str, tuple[str, float]] = {k: (d, sc) for k, d, sc in local}
        for k, d, sc in self._search_cache.get(q, []):
            if sc > merged.get(k, ("", 0.0))[1]:
                merged[k] = (d, sc)
        ranked = sorted(merged.items(), key=lambda kv: (-kv[1][1], kv[0]))
        return [(k, v[0], v[1]) for k, v in ranked[:limit]]

    def labels_for_ingredient(self, ingredient: str, limit: int = 50) -> list[LabelDocument]:
        self._ensure_labels(ingredient)
        return super().labels_for_ingredient(ingredient, limit)

    def classes_for(self, ingredient: str) -> list[DrugClass]:
        self._ensure_labels(ingredient)
        return super().classes_for(ingredient)

    def ingredients_in_class(self, class_id: str, limit: int = 100) -> list[str]:
        if class_id.startswith("EPC:"):
            name = next((c.class_name for cs in self.classes.values() for c in cs if c.class_id == class_id), None)
            if name is None:
                name = next((e for d in self.labels.values() for e in d.pharm_class_epc
                             if "EPC:" + e.lower() == class_id), class_id[4:])
            self._ensure_class_members(name, None, set())
        return super().ingredients_in_class(class_id, limit)

    def shortages(self, status: ShortageStatus | None = None, query: str | None = None, limit: int = 50,
                  offset: int = 0) -> tuple[list[ShortageRecord], int]:
        self._ensure_shortages()
        return super().shortages(status, query, limit, offset)

    def shortages_for_ingredient(self, ingredient: str) -> list[ShortageRecord]:
        self._ensure_shortages()
        return super().shortages_for_ingredient(ingredient)

    def orange_book_for_ingredient(self, ingredient: str) -> list[OrangeBookProduct]:
        self._ensure_products(ingredient)
        return super().orange_book_for_ingredient(ingredient)

    def section_chunks(self, ingredient: str, sections: Sequence[SectionType], limit: int = 40) -> list[Chunk]:
        self._ensure_labels(ingredient)
        return super().section_chunks(ingredient, sections, limit)

    # Ops ----------------------------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        out = super().stats()
        out["live_ingredients_hydrated"] = len(self._labels_done)
        out["live_requests"] = getattr(getattr(self.fetcher, "log", None), "requests", 0)
        return out

    def data_versions(self) -> dict[str, str]:
        out = dict(super().data_versions())
        out["mode"] = "live_openfda"
        if self._shortages_loaded_at:
            out["fda_drug_shortages"] = self._shortages_loaded_at.isoformat()
        if self._label_fetched_at:
            out["openfda_label"] = self._label_fetched_at.isoformat()
        out["live_label_limit"] = str(self.settings.live_label_limit)
        return out

    def readiness(self) -> tuple[bool, dict[str, Any]]:
        """Live mode is ready once the store exists; data arrives with the first request."""
        return True, {"mode": "live_openfda", "recent_errors": self.errors[-5:]}
