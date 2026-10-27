"""In process knowledge store with BM25 and dense search, persisted as a snapshot directory."""

from __future__ import annotations

import difflib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..domain.enums import SectionType, ShortageStatus
from ..domain.models import Chunk, DrugClass, LabelDocument, OrangeBookProduct, ShortageRecord
from ..domain.normalize import active_moiety
from .base import text_hash

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = {
    "the", "of", "and", "or", "a", "an", "in", "to", "for", "with", "is", "are", "be", "by", "on",
    "as", "at", "that", "this", "these", "it", "its", "from", "was", "were", "has", "have", "not",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class MemoryStore:
    BM25_K1 = 1.2
    BM25_B = 0.75

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.labels: dict[str, LabelDocument] = {}
        self.chunk_list: list[Chunk] = []
        self.chunk_index: dict[str, int] = {}
        self.chunk_hash: list[str] = []
        self.vectors: list[np.ndarray] = []
        self._matrix: np.ndarray | None = None
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        self.doc_len: list[int] = []
        self.by_moiety: dict[str, set[int]] = defaultdict(set)
        self.labels_by_moiety: dict[str, set[str]] = defaultdict(set)
        self.shortage_records: list[ShortageRecord] = []
        self.orange: list[OrangeBookProduct] = []
        self.classes: dict[str, list[DrugClass]] = {}
        self.runs: list[dict[str, Any]] = []
        self.audit: dict[str, dict[str, Any]] = {}
        self.dead: set[int] = set()

    # Writes -----------------------------------------------------------------------------------

    def upsert_label(self, doc: LabelDocument, chunks: Sequence[Chunk], embeddings: np.ndarray) -> None:
        existing = self.labels.get(doc.set_id)
        if existing and existing.effective_time and doc.effective_time and existing.effective_time > doc.effective_time:
            return  # keep the newer version already indexed
        if existing:
            for i, c in enumerate(self.chunk_list):
                if c.set_id == doc.set_id:
                    self.dead.add(i)
                    self.chunk_index.pop(c.chunk_id, None)
        self.labels[doc.set_id] = doc
        moieties = {active_moiety(i) for i in doc.ingredients}
        for m in moieties:
            self.labels_by_moiety[m].add(doc.set_id)
        for chunk, vec in zip(chunks, embeddings, strict=True):
            idx = len(self.chunk_list)
            self.chunk_list.append(chunk)
            self.chunk_index[chunk.chunk_id] = idx
            self.chunk_hash.append(text_hash(chunk.text))
            self.vectors.append(np.asarray(vec, dtype=np.float32))
            toks = tokenize(chunk.text)
            self.doc_len.append(len(toks))
            for tok, tf in Counter(toks).items():
                self.postings[tok][idx] = tf
            for m in moieties:
                self.by_moiety[m].add(idx)
        self._matrix = None

    def replace_shortages(self, records: Sequence[ShortageRecord]) -> int:
        self.shortage_records = list(records)
        return len(self.shortage_records)

    def replace_orange_book(self, products: Sequence[OrangeBookProduct]) -> int:
        self.orange = list(products)
        return len(self.orange)

    def upsert_classes(self, ingredient: str, classes: Sequence[DrugClass]) -> None:
        key = active_moiety(ingredient)
        merged = {c.class_id: c for c in self.classes.get(key, [])}
        for c in classes:
            merged[c.class_id] = c
        self.classes[key] = list(merged.values())

    def record_ingestion_run(self, source: str, records: int, details: dict[str, Any]) -> None:
        self.runs.append(
            {"source": source, "records": records, "finished_at": datetime.now(UTC).isoformat(), **details}
        )

    def record_audit(self, request_id: str, request: dict[str, Any], response: dict[str, Any]) -> None:
        self.audit[request_id] = {"request": request, "response": response, "created_at": datetime.now(UTC).isoformat()}

    def commit(self) -> None:
        return None

    # Entities ---------------------------------------------------------------------------------

    def known_ingredients(self) -> set[str]:
        return {m for m, ids in self.labels_by_moiety.items() if ids}

    def search_ingredients(self, term: str, limit: int = 10) -> list[tuple[str, str, float]]:
        q = term.strip().lower()
        if not q:
            return []
        scored: dict[str, tuple[str, float]] = {}

        def consider(key: str, display: str, score: float) -> None:
            if score > scored.get(key, ("", 0.0))[1]:
                scored[key] = (display, score)

        for moiety in self.known_ingredients():
            if moiety == q:
                consider(moiety, moiety, 1.0)
            elif moiety.startswith(q):
                consider(moiety, moiety, 0.9)
            elif q in moiety:
                consider(moiety, moiety, 0.75)
            else:
                r = difflib.SequenceMatcher(None, q, moiety).ratio()
                if r >= 0.78:
                    consider(moiety, moiety, 0.6 * r)
        for doc in self.labels.values():
            for brand in doc.brand_names:
                b = brand.lower()
                if b == q or b.startswith(q):
                    for ing in doc.ingredients:
                        consider(active_moiety(ing), f"{active_moiety(ing)} ({brand})", 0.85 if b != q else 0.95)
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1][1], kv[0]))
        return [(k, v[0], round(v[1], 3)) for k, v in ranked[:limit]]

    def labels_for_ingredient(self, ingredient: str, limit: int = 50) -> list[LabelDocument]:
        ids = self.labels_by_moiety.get(active_moiety(ingredient), set())
        docs = [self.labels[i] for i in ids if i in self.labels]
        docs.sort(key=lambda d: d.effective_time or date.min, reverse=True)
        return docs[:limit]

    def label(self, set_id: str) -> LabelDocument | None:
        return self.labels.get(set_id)

    def chunks(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        return [self.chunk_list[self.chunk_index[c]] for c in chunk_ids if c in self.chunk_index]

    def classes_for(self, ingredient: str) -> list[DrugClass]:
        key = active_moiety(ingredient)
        out = {c.class_id: c for c in self.classes.get(key, [])}
        # EPC classes carried on the labels themselves (openFDA pharm_class_epc)
        for doc in self.labels_for_ingredient(key, limit=25):
            if len(doc.ingredients) != 1:
                continue  # combination products would contaminate single ingredient classes
            for epc in doc.pharm_class_epc:
                cid = "EPC:" + epc.lower()
                out.setdefault(cid, DrugClass(class_id=cid, class_name=epc, class_type="EPC", source="LABEL"))
        return list(out.values())

    def ingredients_in_class(self, class_id: str, limit: int = 100) -> list[str]:
        members: set[str] = set()
        for ing, classes in self.classes.items():
            if any(c.class_id == class_id for c in classes):
                members.add(ing)
        if class_id.startswith("EPC:"):
            target = class_id[4:]
            for doc in self.labels.values():
                if len(doc.ingredients) == 1 and any(e.lower() == target for e in doc.pharm_class_epc):
                    members.add(active_moiety(doc.ingredients[0]))
        return sorted(members)[:limit]

    def shortages(
        self, status: ShortageStatus | None = None, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[ShortageRecord], int]:
        rows = self.shortage_records
        if status:
            rows = [r for r in rows if r.status is status]
        if query:
            q = query.lower()
            rows = [
                r for r in rows
                if q in r.generic_name.lower()
                or any(q in i for i in r.ingredients)
                or q in (r.company_name or "").lower()
                or any(q in c.lower() for c in r.therapeutic_category)
            ]
        rows = sorted(rows, key=lambda r: (r.update_date or date.min), reverse=True)
        return rows[offset : offset + limit], len(rows)

    def shortages_for_ingredient(self, ingredient: str) -> list[ShortageRecord]:
        m = active_moiety(ingredient)
        return [r for r in self.shortage_records if m in r.ingredients]

    def orange_book_for_ingredient(self, ingredient: str) -> list[OrangeBookProduct]:
        m = active_moiety(ingredient)
        return [p for p in self.orange if p.ingredients == [m]]

    # Retrieval --------------------------------------------------------------------------------

    def _candidate_idx(self, ingredients: Sequence[str] | None, sections: Sequence[SectionType] | None) -> set[int] | None:
        idx: set[int] | None = None
        if ingredients:
            idx = set()
            for ing in ingredients:
                idx |= self.by_moiety.get(active_moiety(ing), set())
        if sections:
            allowed = set(sections)
            base = idx if idx is not None else set(range(len(self.chunk_list)))
            idx = {i for i in base if self.chunk_list[i].section in allowed}
        if idx is not None:
            idx -= self.dead
        return idx

    def _dedupe(self, scored: list[tuple[int, float]], k: int) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        seen: set[str] = set()
        for i, s in scored:
            h = self.chunk_hash[i]
            if h in seen:
                continue
            seen.add(h)
            out.append((self.chunk_list[i].chunk_id, float(s)))
            if len(out) >= k:
                break
        return out

    def lexical_search(
        self, query: str, k: int, ingredients: Sequence[str] | None = None, sections: Sequence[SectionType] | None = None
    ) -> list[tuple[str, float]]:
        allowed = self._candidate_idx(ingredients, sections)
        n = len(self.chunk_list) - len(self.dead)
        if n <= 0:
            return []
        avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 1.0
        scores: dict[int, float] = defaultdict(float)
        for tok in set(tokenize(query)):
            plist = self.postings.get(tok)
            if not plist:
                continue
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, tf in plist.items():
                if i in self.dead or (allowed is not None and i not in allowed):
                    continue
                dl = self.doc_len[i]
                scores[i] += idf * tf * (self.BM25_K1 + 1) / (tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * dl / avgdl))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return self._dedupe(ranked, k)

    def vector_search(
        self, vector: np.ndarray, k: int, ingredients: Sequence[str] | None = None, sections: Sequence[SectionType] | None = None
    ) -> list[tuple[str, float]]:
        if not self.vectors:
            return []
        if self._matrix is None:
            self._matrix = np.vstack(self.vectors)
        allowed = self._candidate_idx(ingredients, sections)
        if allowed is not None and not allowed:
            return []
        if allowed is None:
            idx = np.array([i for i in range(len(self.chunk_list)) if i not in self.dead])
        else:
            idx = np.array(sorted(allowed))
        sims = self._matrix[idx] @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-sims)
        return self._dedupe([(int(idx[o]), float(sims[o])) for o in order[: k * 4]], k)

    def section_chunks(self, ingredient: str, sections: Sequence[SectionType], limit: int = 40) -> list[Chunk]:
        idx = self._candidate_idx([ingredient], sections) or set()
        ordered = sorted(
            idx,
            key=lambda i: (
                -(self.chunk_list[i].effective_time or date.min).toordinal(),
                self.chunk_list[i].set_id,
                self.chunk_list[i].ordinal,
            ),
        )
        out: list[Chunk] = []
        seen: set[str] = set()
        for i in ordered:
            h = self.chunk_hash[i]
            if h in seen:
                continue
            seen.add(h)
            out.append(self.chunk_list[i])
            if len(out) >= limit:
                break
        return out

    # Ops --------------------------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {
            "labels": len(self.labels),
            "chunks": len(self.chunk_list) - len(self.dead),
            "ingredients": len(self.known_ingredients()),
            "shortage_records": len(self.shortage_records),
            "current_shortages": sum(1 for r in self.shortage_records if r.status is ShortageStatus.CURRENT),
            "orange_book_products": len(self.orange),
            "class_links": sum(len(v) for v in self.classes.values()),
        }

    def data_versions(self) -> dict[str, str]:
        latest: dict[str, str] = {}
        for run in self.runs:
            latest[run["source"]] = run["finished_at"]
        return latest

    def get_audit(self, request_id: str) -> dict[str, Any] | None:
        return self.audit.get(request_id)

    # Snapshot persistence ---------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        live = [i for i in range(len(self.chunk_list)) if i not in self.dead]
        with (path / "labels.jsonl").open("w") as fh:
            for d in self.labels.values():
                fh.write(d.model_dump_json() + "\n")
        with (path / "chunks.jsonl").open("w") as fh:
            for i in live:
                fh.write(self.chunk_list[i].model_dump_json() + "\n")
        np.save(path / "embeddings.npy", np.vstack([self.vectors[i] for i in live]) if live else np.zeros((0, self.dim)))
        with (path / "shortages.jsonl").open("w") as fh:
            for r in self.shortage_records:
                fh.write(r.model_dump_json() + "\n")
        with (path / "orange_book.jsonl").open("w") as fh:
            for p in self.orange:
                fh.write(p.model_dump_json() + "\n")
        (path / "classes.json").write_text(
            json.dumps({k: [c.model_dump() for c in v] for k, v in self.classes.items()})
        )
        (path / "runs.json").write_text(json.dumps(self.runs))

    def load(self, path: Path) -> None:
        labels = [LabelDocument.model_validate_json(line) for line in (path / "labels.jsonl").read_text().splitlines() if line]
        chunks = [Chunk.model_validate_json(line) for line in (path / "chunks.jsonl").read_text().splitlines() if line]
        vectors = np.load(path / "embeddings.npy")
        by_set: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            by_set[c.set_id].append(i)
        for doc in labels:
            ids = by_set.get(doc.set_id, [])
            self.upsert_label(doc, [chunks[i] for i in ids], vectors[ids] if ids else np.zeros((0, self.dim)))
        if (path / "shortages.jsonl").exists():
            self.replace_shortages(
                [ShortageRecord.model_validate_json(x) for x in (path / "shortages.jsonl").read_text().splitlines() if x]
            )
        if (path / "orange_book.jsonl").exists():
            self.replace_orange_book(
                [OrangeBookProduct.model_validate_json(x) for x in (path / "orange_book.jsonl").read_text().splitlines() if x]
            )
        if (path / "classes.json").exists():
            for k, v in json.loads((path / "classes.json").read_text()).items():
                self.classes[k] = [DrugClass.model_validate(c) for c in v]
        if (path / "runs.json").exists():
            self.runs = json.loads((path / "runs.json").read_text())
