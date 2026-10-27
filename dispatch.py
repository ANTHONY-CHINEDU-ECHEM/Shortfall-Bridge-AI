"""A framework free router over ``ApiHandlers``.

The in browser runtime has no web server, so the frontend's API calls are posted to a Web Worker
as (method, url, body) and answered here with the same status codes, error shapes and payloads as
the FastAPI app. Keeping this table next to ``app.py`` and testing both against the same requests
is what guarantees the hosted demo behaves like a real deployment.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ValidationError

from ..domain.models import PatientContext, ReviewRequest, SubstitutionRequest
from .handlers import ApiError, ApiHandlers
from .schemas import BatchRequest, ConvertRequest


@dataclass
class DispatchResult:
    status: int
    body: Any
    content_type: str = "application/json"

    def to_json(self) -> str:
        body = self.body
        if isinstance(body, BaseModel):
            body = body.model_dump(mode="json")
        elif isinstance(body, list):
            body = [b.model_dump(mode="json") if isinstance(b, BaseModel) else b for b in body]
        return json.dumps({"status": self.status, "content_type": self.content_type, "body": body}, default=str)


Handler = Callable[[ApiHandlers, dict[str, str], dict[str, str], Any], Any]


def _int(q: dict[str, str], key: str, default: int) -> int:
    try:
        return int(q.get(key, default))
    except ValueError as exc:
        raise ApiError(422, f"{key} must be an integer") from exc


def _model(cls: type[BaseModel], body: Any) -> Any:
    return cls.model_validate(body if body is not None else {})


ROUTES: list[tuple[str, re.Pattern[str], Handler]] = [
    ("GET", re.compile(r"^/health$"), lambda h, p, q, b: {"status": "ok"}),
    ("GET", re.compile(r"^/api/v1/meta$"), lambda h, p, q, b: h.meta()),
    ("GET", re.compile(r"^/api/v1/drugs/search$"),
     lambda h, p, q, b: h.search_drugs(q.get("q", ""), _int(q, "limit", 8))),
    ("GET", re.compile(r"^/api/v1/shortages/insights$"), lambda h, p, q, b: h.insights()),
    ("GET", re.compile(r"^/api/v1/shortages$"),
     lambda h, p, q, b: h.shortages(q.get("status"), q.get("q"), _int(q, "limit", 25), _int(q, "offset", 0))),
    ("GET", re.compile(r"^/api/v1/evidence/(?P<chunk_id>[\w-]+)$"), lambda h, p, q, b: h.evidence(p["chunk_id"])),
    ("GET", re.compile(r"^/api/v1/equivalence/tables$"), lambda h, p, q, b: h.equivalence_tables()),
    ("POST", re.compile(r"^/api/v1/equivalence/convert$"), lambda h, p, q, b: h.convert(_model(ConvertRequest, b))),
    ("POST", re.compile(r"^/api/v1/renal/estimate$"), lambda h, p, q, b: h.renal(_model(PatientContext, b))),
    ("GET", re.compile(r"^/api/v1/audit/verify$"), lambda h, p, q, b: h.audit_verify()),
    ("GET", re.compile(r"^/api/v1/audit/(?P<rid>[\w-]+)/note$"),
     lambda h, p, q, b: DispatchResult(200, h.note(p["rid"], q.get("format", "markdown")),
                                       "text/markdown" if q.get("format", "markdown") == "markdown" else "text/plain")),
    ("GET", re.compile(r"^/api/v1/audit/(?P<rid>[\w-]+)$"), lambda h, p, q, b: h.audit(p["rid"])),
    ("POST", re.compile(r"^/api/v1/reviews$"), lambda h, p, q, b: DispatchResult(201, h.review(_model(ReviewRequest, b)))),
    ("GET", re.compile(r"^/api/v1/reviews/summary$"), lambda h, p, q, b: h.review_summary()),
    ("GET", re.compile(r"^/api/v1/reviews$"), lambda h, p, q, b: h.reviews(q.get("request_id"), _int(q, "limit", 200))),
    ("POST", re.compile(r"^/api/v1/substitutions/batch$"), lambda h, p, q, b: h.batch(_model(BatchRequest, b))),
    ("POST", re.compile(r"^/api/v1/substitutions$"), lambda h, p, q, b: h.recommend(_model(SubstitutionRequest, b))),
    ("POST", re.compile(r"^/api/v1/fhir/substitutions$"),
     lambda h, p, q, b: h.fhir_substitution(b or {}, q.get("order_id"))),
    ("GET", re.compile(r"^/cds-services$"), lambda h, p, q, b: h.cds_discovery()),
    ("POST", re.compile(r"^/cds-services/(?P<sid>[\w-]+)$"), lambda h, p, q, b: h.cds_hook(p["sid"], b or {})),
]


def _validation_detail(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": ["body", *[str(x) for x in e.get("loc", ())]], "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()]


def dispatch(handlers: ApiHandlers, method: str, url: str, body: Any = None) -> DispatchResult:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    if isinstance(body, str):
        try:
            body = json.loads(body) if body else None
        except json.JSONDecodeError:
            return DispatchResult(422, {"detail": "Request body is not valid JSON"})
    allowed = False
    for m, pattern, fn in ROUTES:
        match = pattern.match(path)
        if not match:
            continue
        allowed = True
        if m != method.upper():
            continue
        try:
            out = fn(handlers, match.groupdict(), query, body)
        except ApiError as exc:
            return DispatchResult(exc.status, {"detail": exc.detail})
        except ValidationError as exc:
            return DispatchResult(422, {"detail": _validation_detail(exc)})
        return out if isinstance(out, DispatchResult) else DispatchResult(200, out)
    return DispatchResult(405 if allowed else 404, {"detail": "Method Not Allowed" if allowed else "Not Found"})
