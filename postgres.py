"""PostgreSQL + pgvector adapter for the knowledge store port."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import resources
from typing import Any

import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from ..domain.enums import SectionType, ShortageStatus
from ..domain.models import Chunk, DrugClass, LabelDocument, OrangeBookProduct, ReviewRecord, ShortageRecord
from ..domain.normalize import active_moiety
from .base import text_hash
from .ledger import GENESIS, chain_hash, verify_chain


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in np.asarray(v).ravel()) + "]"


def _label_from_row(r: Any) -> LabelDocument:
    m = r._mapping
    return LabelDocument(
        set_id=m["set_id"], spl_id=m["spl_id"], version=m["version"], effective_time=m["effective_time"],
        title=m["title"], generic_names=list(m["generic_names"]), brand_names=list(m["brand_names"]),
        ingredients=list(m["ingredients"]), manufacturer=m["manufacturer"], routes=list(m["routes"]),
        product_type=m["product_type"], rxcuis=list(m["rxcuis"]), pharm_class_epc=list(m["pharm_class_epc"]),
        pharm_class_moa=list(m["pharm_class_moa"]), application_numbers=list(m["application_numbers"]),
        source=m["source"],
    )


def _chunk_from_row(r: Any) -> Chunk:
    m = r._mapping
    return Chunk(
        chunk_id=m["chunk_id"], set_id=m["set_id"], section=SectionType(m["section"]), ordinal=m["ordinal"],
        text=m["text"], ingredients=list(m["moieties"]), title=m["title"], effective_time=m["effective_time"],
    )


def _shortage_from_row(r: Any) -> ShortageRecord:
    m = dict(r._mapping)
    m["status"] = ShortageStatus.parse(m["status"])
    return ShortageRecord.model_validate(m)


class PostgresStore:
    def __init__(self, url: str, dim: int = 384) -> None:
        self.dim = dim
        self.engine: Engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
        self._conn: Connection | None = None
        self.ensure_schema()

    # Connection management --------------------------------------------------------------------

    def ensure_schema(self) -> None:
        ddl = resources.files("shortfall_bridge.store").joinpath("schema.sql").read_text()
        ddl = ddl.replace("%(dim)s", str(int(self.dim)))
        with self.engine.begin() as conn:
            for raw in ddl.split(";"):
                stmt = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("--")).strip()
                if stmt:
                    conn.exec_driver_sql(stmt)

    @property
    def conn(self) -> Connection:
        if self._conn is None or self._conn.closed:
            self._conn = self.engine.connect()
            self._conn.begin()
        return self._conn

    def commit(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    # Writes -----------------------------------------------------------------------------------

    def upsert_label(self, doc: LabelDocument, chunks: Sequence[Chunk], embeddings: np.ndarray) -> None:
        c = self.conn
        current = c.execute(
            text("SELECT effective_time FROM label_documents WHERE set_id = :s"), {"s": doc.set_id}
        ).scalar()
        if current and doc.effective_time and current > doc.effective_time:
            return
        payload = doc.model_dump()
        payload["moieties"] = sorted({active_moiety(i) for i in doc.ingredients})
        c.execute(text("DELETE FROM label_documents WHERE set_id = :s"), {"s": doc.set_id})
        c.execute(
            text(
                """INSERT INTO label_documents (set_id, spl_id, version, effective_time, title, generic_names,
                brand_names, ingredients, moieties, manufacturer, routes, product_type, rxcuis, pharm_class_epc,
                pharm_class_moa, application_numbers, source) VALUES (:set_id, :spl_id, :version, :effective_time,
                :title, :generic_names, :brand_names, :ingredients, :moieties, :manufacturer, :routes, :product_type,
                :rxcuis, :pharm_class_epc, :pharm_class_moa, :application_numbers, :source)"""
            ),
            payload,
        )
        rows = [
            {
                "chunk_id": ch.chunk_id, "set_id": ch.set_id, "section": ch.section.value, "ordinal": ch.ordinal,
                "text": ch.text, "text_hash": text_hash(ch.text), "moieties": payload["moieties"],
                "title": ch.title, "effective_time": ch.effective_time, "embedding": _vec_literal(vec),
            }
            for ch, vec in zip(chunks, embeddings, strict=True)
        ]
        if rows:
            c.execute(
                text(
                    """INSERT INTO label_chunks (chunk_id, set_id, section, ordinal, text, text_hash, moieties, title,
                    effective_time, embedding) VALUES (:chunk_id, :set_id, :section, :ordinal, :text, :text_hash,
                    :moieties, :title, :effective_time, CAST(:embedding AS vector))"""
                ),
                rows,
            )

    def replace_shortages(self, records: Sequence[ShortageRecord]) -> int:
        c = self.conn
        c.execute(text("TRUNCATE drug_shortages"))
        if records:
            rows = []
            for r in records:
                d = r.model_dump()
                d["status"] = r.status.value
                rows.append(d)
            cols = list(rows[0].keys())
            c.execute(
                text(
                    f"INSERT INTO drug_shortages ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)}) "
                    "ON CONFLICT (record_id) DO NOTHING"
                ),
                rows,
            )
        return len(records)

    def replace_orange_book(self, products: Sequence[OrangeBookProduct]) -> int:
        c = self.conn
        c.execute(text("TRUNCATE orange_book_products"))
        rows = []
        for p in products:
            d = p.model_dump()
            d["moieties"] = d.pop("ingredients")
            rows.append(d)
        for i in range(0, len(rows), 5000):
            batch = rows[i : i + 5000]
            cols = list(batch[0].keys())
            c.execute(
                text(f"INSERT INTO orange_book_products ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)})"),
                batch,
            )
        return len(rows)

    def upsert_classes(self, ingredient: str, classes: Sequence[DrugClass]) -> None:
        m = active_moiety(ingredient)
        for cl in classes:
            self.conn.execute(
                text(
                    """INSERT INTO ingredient_classes (moiety, class_id, class_name, class_type, source)
                    VALUES (:m, :id, :name, :type, :src) ON CONFLICT (moiety, class_id) DO UPDATE
                    SET class_name = EXCLUDED.class_name, class_type = EXCLUDED.class_type"""
                ),
                {"m": m, "id": cl.class_id, "name": cl.class_name, "type": cl.class_type, "src": cl.source},
            )

    def record_ingestion_run(self, source: str, records: int, details: dict[str, Any]) -> None:
        self.conn.execute(
            text("INSERT INTO ingestion_runs (source, records, details) VALUES (:s, :r, CAST(:d AS jsonb))"),
            {"s": source, "r": records, "d": json.dumps(details, default=str)},
        )

    def record_audit(self, request_id: str, request: dict[str, Any], response: dict[str, Any]) -> None:
        """Append to the hash chain. An advisory lock serialises writers so the chain never forks."""
        ts = datetime.now(UTC).isoformat()
        req_json, resp_json = json.dumps(request, default=str), json.dumps(response, default=str)
        # Hash what will be read back: JSONB round trips through json, so hash the decoded payload.
        req_obj, resp_obj = json.loads(req_json), json.loads(resp_json)
        with self.engine.begin() as c:
            c.exec_driver_sql("SELECT pg_advisory_xact_lock(724519)")
            prev = c.execute(text(
                "SELECT record_hash FROM recommendation_audit WHERE record_hash IS NOT NULL ORDER BY seq DESC LIMIT 1"
            )).scalar() or GENESIS
            c.execute(
                text(
                    "INSERT INTO recommendation_audit (request_id, request, response, prev_hash, record_hash, chain_ts) "
                    "VALUES (:id, CAST(:q AS jsonb), CAST(:r AS jsonb), :prev, :h, :ts) ON CONFLICT DO NOTHING"
                ),
                {"id": request_id, "q": req_json, "r": resp_json, "prev": prev, "ts": ts,
                 "h": chain_hash(prev, request_id, req_obj, resp_obj, ts)},
            )

    def record_review(self, review: ReviewRecord) -> None:
        d = review.model_dump(mode="json")
        with self.engine.begin() as c:
            c.execute(
                text(
                    """INSERT INTO pharmacist_reviews (review_id, request_id, candidate_ingredient, candidate_tier,
                    decision, reason, chosen_alternative, reviewer_role, rank, created_at) VALUES (:review_id,
                    :request_id, :candidate_ingredient, :candidate_tier, :decision, :reason, :chosen_alternative,
                    :reviewer_role, :rank, :created_at)"""
                ),
                d,
            )

    def reviews(self, request_id: str | None = None, limit: int = 500) -> list[ReviewRecord]:
        where = "WHERE request_id = :r" if request_id else ""
        rows = self._q(f"SELECT * FROM pharmacist_reviews {where} ORDER BY created_at DESC LIMIT :l",
                       r=request_id, l=limit)
        return [ReviewRecord.model_validate(dict(r._mapping)) for r in rows]

    def verify_audit_chain(self) -> dict[str, Any]:
        rows = self._q("SELECT request_id, request, response, chain_ts, prev_hash, record_hash "
                       "FROM recommendation_audit ORDER BY seq")
        return verify_chain(
            {"request_id": r[0], "request": r[1], "response": r[2], "created_at": r[3], "prev_hash": r[4],
             "record_hash": r[5]} for r in rows
        )

    # Reads ------------------------------------------------------------------------------------

    def _q(self, sql: str, **params: Any) -> list[Any]:
        with self.engine.connect() as c:
            return list(c.execute(text(sql), params))

    def known_ingredients(self) -> set[str]:
        return {r[0] for r in self._q("SELECT DISTINCT unnest(moieties) FROM label_documents")}

    def search_ingredients(self, term: str, limit: int = 10) -> list[tuple[str, str, float]]:
        rows = self._q(
            """
            WITH m AS (SELECT DISTINCT unnest(moieties) AS moiety FROM label_documents)
            SELECT moiety, moiety AS display,
                   CASE WHEN moiety = :t THEN 1.0 WHEN moiety LIKE :p THEN 0.9 ELSE similarity(moiety, :t) END AS score
            FROM m WHERE moiety LIKE :p OR similarity(moiety, :t) > 0.35
            UNION ALL
            SELECT unnest(moieties), unnest(moieties) || ' (' || b || ')', 0.85
            FROM label_documents, unnest(brand_names) AS b WHERE lower(b) LIKE :p
            ORDER BY score DESC LIMIT :lim
            """,
            t=term.lower(), p=term.lower() + "%", lim=limit * 3,
        )
        out: dict[str, tuple[str, float]] = {}
        for k, display, score in rows:
            if k not in out or score > out[k][1]:
                out[k] = (display, float(score))
        return [(k, v[0], round(v[1], 3)) for k, v in sorted(out.items(), key=lambda kv: -kv[1][1])[:limit]]

    def labels_for_ingredient(self, ingredient: str, limit: int = 50) -> list[LabelDocument]:
        rows = self._q(
            "SELECT * FROM label_documents WHERE moieties @> ARRAY[:m] ORDER BY effective_time DESC NULLS LAST LIMIT :l",
            m=active_moiety(ingredient), l=limit,
        )
        return [_label_from_row(r) for r in rows]

    def label(self, set_id: str) -> LabelDocument | None:
        rows = self._q("SELECT * FROM label_documents WHERE set_id = :s", s=set_id)
        return _label_from_row(rows[0]) if rows else None

    def chunks(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        rows = self._q("SELECT * FROM label_chunks WHERE chunk_id = ANY(:ids)", ids=list(chunk_ids))
        by_id = {r._mapping["chunk_id"]: _chunk_from_row(r) for r in rows}
        return [by_id[c] for c in chunk_ids if c in by_id]

    def classes_for(self, ingredient: str) -> list[DrugClass]:
        m = active_moiety(ingredient)
        rows = self._q("SELECT class_id, class_name, class_type, source FROM ingredient_classes WHERE moiety = :m", m=m)
        out = {r[0]: DrugClass(class_id=r[0], class_name=r[1], class_type=r[2], source=r[3]) for r in rows}
        epc_rows = self._q(
            """SELECT DISTINCT unnest(pharm_class_epc) FROM label_documents
               WHERE moieties = ARRAY[:m]::text[]""",
            m=m,
        )
        for (epc,) in epc_rows:
            cid = "EPC:" + epc.lower()
            out.setdefault(cid, DrugClass(class_id=cid, class_name=epc, class_type="EPC", source="LABEL"))
        return list(out.values())

    def ingredients_in_class(self, class_id: str, limit: int = 100) -> list[str]:
        members = {r[0] for r in self._q("SELECT moiety FROM ingredient_classes WHERE class_id = :c", c=class_id)}
        if class_id.startswith("EPC:"):
            rows = self._q(
                """SELECT DISTINCT moieties[1] FROM label_documents
                   WHERE cardinality(moieties) = 1 AND EXISTS (
                     SELECT 1 FROM unnest(pharm_class_epc) e WHERE lower(e) = :n)""",
                n=class_id[4:],
            )
            members |= {r[0] for r in rows}
        return sorted(members)[:limit]

    def shortages(
        self, status: ShortageStatus | None = None, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[ShortageRecord], int]:
        where, params = ["TRUE"], {"l": limit, "o": offset}
        if status:
            where.append("status = :st")
            params["st"] = status.value
        if query:
            where.append(
                "(generic_name ILIKE :q OR company_name ILIKE :q OR EXISTS (SELECT 1 FROM unnest(ingredients) i "
                "WHERE i ILIKE :q) OR EXISTS (SELECT 1 FROM unnest(therapeutic_category) t WHERE t ILIKE :q))"
            )
            params["q"] = f"%{query}%"
        clause = " AND ".join(where)
        total = self._q(f"SELECT count(*) FROM drug_shortages WHERE {clause}", **params)[0][0]
        rows = self._q(
            f"SELECT * FROM drug_shortages WHERE {clause} ORDER BY update_date DESC NULLS LAST LIMIT :l OFFSET :o",
            **params,
        )
        return [_shortage_from_row(r) for r in rows], int(total)

    def shortages_for_ingredient(self, ingredient: str) -> list[ShortageRecord]:
        rows = self._q("SELECT * FROM drug_shortages WHERE ingredients @> ARRAY[:m]", m=active_moiety(ingredient))
        return [_shortage_from_row(r) for r in rows]

    def orange_book_for_ingredient(self, ingredient: str) -> list[OrangeBookProduct]:
        rows = self._q(
            "SELECT * FROM orange_book_products WHERE moieties = ARRAY[:m]::text[]", m=active_moiety(ingredient)
        )
        out = []
        for r in rows:
            d = dict(r._mapping)
            d.pop("id", None)
            d["ingredients"] = d.pop("moieties")
            out.append(OrangeBookProduct.model_validate(d))
        return out

    # Retrieval --------------------------------------------------------------------------------

    @staticmethod
    def _filters(ingredients: Sequence[str] | None, sections: Sequence[SectionType] | None) -> tuple[str, dict[str, Any]]:
        where, params = [], {}
        if ingredients:
            where.append("moieties && :ings")
            params["ings"] = [active_moiety(i) for i in ingredients]
        if sections:
            where.append("section = ANY(:secs)")
            params["secs"] = [s.value for s in sections]
        return (" AND ".join(where) or "TRUE"), params

    def lexical_search(
        self, query: str, k: int, ingredients: Sequence[str] | None = None, sections: Sequence[SectionType] | None = None
    ) -> list[tuple[str, float]]:
        clause, params = self._filters(ingredients, sections)
        rows = self._q(
            f"""
            SELECT DISTINCT ON (text_hash) chunk_id, score FROM (
              SELECT chunk_id, text_hash, ts_rank_cd(tsv, q, 32) AS score
              FROM label_chunks, websearch_to_tsquery('english', :query) q
              WHERE tsv @@ q AND {clause}
              ORDER BY score DESC LIMIT :lim
            ) s ORDER BY text_hash, score DESC
            """,
            query=query, lim=k * 4, **params,
        )
        return sorted(((r[0], float(r[1])) for r in rows), key=lambda x: -x[1])[:k]

    def vector_search(
        self, vector: np.ndarray, k: int, ingredients: Sequence[str] | None = None, sections: Sequence[SectionType] | None = None
    ) -> list[tuple[str, float]]:
        clause, params = self._filters(ingredients, sections)
        with self.engine.connect() as c:
            c.exec_driver_sql("SET LOCAL hnsw.ef_search = 100")
            rows = list(
                c.execute(
                    text(
                        f"""
                        SELECT chunk_id, text_hash, 1 - (embedding <=> CAST(:v AS vector)) AS sim
                        FROM label_chunks WHERE {clause}
                        ORDER BY embedding <=> CAST(:v AS vector) LIMIT :lim
                        """
                    ),
                    {"v": _vec_literal(vector), "lim": k * 4, **params},
                )
            )
        out, seen = [], set()
        for cid, h, sim in rows:
            if h in seen:
                continue
            seen.add(h)
            out.append((cid, float(sim)))
        return out[:k]

    def section_chunks(self, ingredient: str, sections: Sequence[SectionType], limit: int = 40) -> list[Chunk]:
        rows = self._q(
            """SELECT DISTINCT ON (text_hash) * FROM label_chunks
               WHERE moieties @> ARRAY[:m] AND section = ANY(:secs)
               ORDER BY text_hash, effective_time DESC NULLS LAST LIMIT :l""",
            m=active_moiety(ingredient), secs=[s.value for s in sections], l=limit,
        )
        return [_chunk_from_row(r) for r in rows]

    # Ops --------------------------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        q = {
            "labels": "SELECT count(*) FROM label_documents",
            "chunks": "SELECT count(*) FROM label_chunks",
            "ingredients": "SELECT count(DISTINCT m) FROM label_documents, unnest(moieties) m",
            "shortage_records": "SELECT count(*) FROM drug_shortages",
            "current_shortages": "SELECT count(*) FROM drug_shortages WHERE status = 'Current'",
            "orange_book_products": "SELECT count(*) FROM orange_book_products",
            "class_links": "SELECT count(*) FROM ingredient_classes",
        }
        return {k: int(self._q(sql)[0][0]) for k, sql in q.items()}

    def data_versions(self) -> dict[str, str]:
        rows = self._q("SELECT source, max(finished_at) FROM ingestion_runs GROUP BY source")
        return {r[0]: r[1].isoformat() for r in rows}

    def get_audit(self, request_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT request, response, created_at, seq, prev_hash, record_hash FROM recommendation_audit "
                       "WHERE request_id = :i", i=request_id)
        if not rows:
            return None
        req, resp, created, seq, prev, h = rows[0]
        return {"request": req, "response": resp, "created_at": created.isoformat(), "seq": seq,
                "prev_hash": prev, "record_hash": h}
