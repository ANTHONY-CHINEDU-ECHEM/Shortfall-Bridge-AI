"""The complete engine running inside the browser, on live openFDA data.

This module is loaded by the frontend's Web Worker through Pyodide. It builds the normal
container (live store, hashing embedder, template narrative) and answers the same API paths as the
server through ``api.dispatch``. Nothing clinical is reimplemented in JavaScript.

Networking
    * The prefetch plan runs first with parallel ``fetch`` calls (``pyodide.http.pyfetch``), so a
      recommendation costs three or four round trips.
    * Anything the plan did not cover is fetched lazily with synchronous XMLHttpRequest, which
      browsers permit inside Web Workers. The UI thread is never blocked.
    * Responses are cached in memory with the same TTLs as the server.

The base URL is chosen by the worker: the site's own ``/api/fda`` edge proxy on Netlify (shared
cache, optional server side API key), otherwise ``https://api.fda.gov`` directly (openFDA allows
cross origin requests). A user supplied openFDA key raises the anonymous limit of 1,000 requests a
day per IP address.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..api.container import Container
from ..api.dispatch import dispatch
from ..api.handlers import ApiHandlers
from ..config import Settings
from ..domain.models import SubstitutionRequest
from ..embeddings import HashingEmbedder
from ..interop.fhir import FhirMappingError, map_fhir
from ..sources.openfda_live import FetchLog, FetchRequest
from ..store.live import LiveOpenFDAStore, Plan

RATE_LIMIT_HINT = ("openFDA rate limit reached. Anonymous use allows 1,000 requests a day per IP address; add a free "
                   "openFDA API key in the demo settings to raise it.")


class BrowserFetcher:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.api_key = api_key or None
        self.log = FetchLog()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.on_request: Any = None  # optional JS callback for progress reporting

    def url(self, req: FetchRequest) -> str:
        return f"{req.url(self.base)}?{urlencode(req.as_params(self.api_key))}"

    def _cached(self, url: str, ttl: float | None) -> dict[str, Any] | None:
        hit = self._cache.get(url)
        if hit and (ttl is None or time.time() - hit[0] <= ttl):
            return hit[1]
        return None

    def _store(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("_error"):
            self._cache[url] = (time.time(), data)
        return data

    def _interpret(self, req: FetchRequest, status: int, text: str) -> dict[str, Any]:
        if status == 200:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"results": [], "_error": f"{req.kind}: invalid JSON from openFDA"}
        if status == 404:
            return {"results": [], "_not_found": True}
        if status == 429:
            return {"results": [], "_error": f"{req.kind}: {RATE_LIMIT_HINT}"}
        if status == 0:
            return {"results": [], "_error": f"{req.kind}: network error or blocked request"}
        return {"results": [], "_error": f"{req.kind}: openFDA HTTP {status}"}

    def _notify(self, req: FetchRequest) -> None:
        self.log.requests += 1
        if self.on_request is not None:
            try:
                self.on_request(req.kind)
            except Exception:  # progress reporting must never break a request
                self.on_request = None

    # Synchronous path (lazy fetches inside the worker) ------------------------------------------

    def get_json(self, req: FetchRequest) -> dict[str, Any]:
        url = self.url(req)
        hit = self._cached(url, req.ttl_s)
        if hit is not None:
            return hit
        self._notify(req)
        from js import XMLHttpRequest  # type: ignore[import-not-found]

        xhr = XMLHttpRequest.new()
        xhr.open("GET", url, False)
        try:
            xhr.send(None)
            status, text = int(xhr.status), str(xhr.responseText or "")
        except Exception as exc:  # network failure surfaces as status 0
            status, text = 0, str(exc)
        data = self._interpret(req, status, text)
        if data.get("_error"):
            self.log.errors.append(str(data["_error"]))
        return self._store(url, data)

    def get_many(self, reqs: Sequence[FetchRequest]) -> list[dict[str, Any]]:
        return [self.get_json(r) for r in reqs]

    # Asynchronous path (prefetch plan, parallel fetch) ------------------------------------------

    async def aget_json(self, req: FetchRequest) -> dict[str, Any]:
        url = self.url(req)
        hit = self._cached(url, req.ttl_s)
        if hit is not None:
            return hit
        self._notify(req)
        from pyodide.http import pyfetch  # type: ignore[import-not-found]

        try:
            resp = await pyfetch(url)
            status, text = int(resp.status), await resp.string()
        except Exception as exc:
            status, text = 0, str(exc)
        data = self._interpret(req, status, text)
        if data.get("_error"):
            self.log.errors.append(str(data["_error"]))
        return self._store(url, data)

    async def aget_many(self, reqs: Sequence[FetchRequest]) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(6)  # stay well inside openFDA's 240 requests per minute

        async def one(r: FetchRequest) -> dict[str, Any]:
            async with sem:
                return await self.aget_json(r)

        return list(await asyncio.gather(*(one(r) for r in reqs)))


async def arun_plan(plan: Plan, fetcher: BrowserFetcher) -> int:
    n = 0
    try:
        batch = next(plan)
        while True:
            n += len(batch)
            batch = plan.send(await fetcher.aget_many(batch))
    except StopIteration:
        return n


class BrowserRuntime:
    def __init__(self, base_url: str = "https://api.fda.gov", api_key: str | None = None, label_limit: int = 5,
                 class_members: int = 6, partners: int = 6) -> None:
        self.settings = Settings(
            environment="production", store_backend="live", embedding_backend="hashing", llm_provider="none",
            log_json=False, log_level="WARNING", data_dir=Path("/tmp/sfb"), openfda_base_url=base_url,
            live_label_limit=label_limit, live_max_class_members=class_members, live_max_partners=partners,
            api_rate_limit_per_minute=10_000,
        )
        self.fetcher = BrowserFetcher(base_url, api_key)
        embedder = HashingEmbedder(self.settings.embedding_dim)
        self.store = LiveOpenFDAStore(self.fetcher, embedder, self.settings)
        self.container = Container.build(self.settings, store=self.store, embedder=embedder, online_resolution=False)
        self.handlers = ApiHandlers(self.container, runtime="browser")

    def set_api_key(self, key: str | None) -> None:
        self.fetcher.api_key = key or None

    def set_progress_callback(self, cb: Any) -> None:
        self.fetcher.on_request = cb

    async def _prefetch(self, req: SubstitutionRequest) -> None:
        try:
            await arun_plan(self.store.prefetch_plan(req), self.fetcher)
        except Exception as exc:  # lazy fetching still covers anything missed
            self.store.errors.append(f"prefetch: {str(exc)[:160]}")

    async def _prefetch_for(self, method: str, path: str, body: Any) -> None:
        if method != "POST" or not isinstance(body, dict):
            return
        reqs: list[SubstitutionRequest] = []
        try:
            if path.endswith("/api/v1/substitutions"):
                reqs = [SubstitutionRequest.model_validate(body)]
            elif path.endswith("/api/v1/substitutions/batch"):
                reqs = [SubstitutionRequest.model_validate(r) for r in body.get("requests", [])[:25]]
            elif path.endswith("/api/v1/fhir/substitutions") or path.startswith("/cds-services/"):
                payload = body if path.endswith("substitutions") else {
                    "draftOrders": (body.get("context") or {}).get("draftOrders"), "prefetch": body.get("prefetch")}
                mapping = map_fhir(payload)
                reqs = [mapping.request] if mapping.request else []
        except (ValueError, FhirMappingError):
            return  # validation errors are reported by the dispatcher with proper status codes
        for r in reqs:
            await self._prefetch(r)

    async def request(self, method: str, url: str, body: str | None = None) -> str:
        """Answer one API call. Returns a JSON envelope: {status, content_type, body}."""
        parsed_body: Any = None
        if body:
            try:
                parsed_body = json.loads(body)
            except json.JSONDecodeError:
                parsed_body = body
        path = url.split("?", 1)[0]
        await self._prefetch_for(method.upper(), path, parsed_body)
        return dispatch(self.handlers, method, url, parsed_body).to_json()

    def request_sync(self, method: str, url: str, body: str | None = None) -> str:
        return dispatch(self.handlers, method, url, body).to_json()

    def stats(self) -> str:
        return json.dumps({"requests": self.fetcher.log.requests, "errors": self.fetcher.log.errors[-5:],
                           "corpus": self.store.stats()})
