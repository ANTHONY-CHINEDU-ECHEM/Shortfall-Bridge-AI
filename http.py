"""Resilient HTTP access for public data sources.

Responsibilities: retries with exponential backoff on transient failures and HTTP 429/5xx,
client side rate limiting (NLM asks RxNav clients to stay below 20 requests per second),
an on disk response cache keyed by URL so repeated ingestion runs are cheap and reproducible,
and streamed downloads with checksum recording for bulk files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from ..config import Settings
from ..logging_setup import log_event

logger = logging.getLogger(__name__)

USER_AGENT = "ShortfallBridgeAI/1.0 (drug shortage substitution research; contact via repository issues)"


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504}
    return isinstance(exc, httpx.TransportError)


class RateLimiter:
    """Thread safe token interval limiter."""

    def __init__(self, per_second: float) -> None:
        self.min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._last + self.min_interval - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


class HttpClient:
    def __init__(
        self,
        settings: Settings,
        cache_dir: Path | None = None,
        requests_per_second: float = 0.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = RateLimiter(requests_per_second)
        self._client = httpx.Client(
            timeout=settings.http_timeout_s,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"},
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Cache ------------------------------------------------------------------------------------

    def _cache_path(self, url: str, params: dict[str, Any] | None) -> Path | None:
        if not self.cache_dir:
            return None
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    # Requests ---------------------------------------------------------------------------------

    def get_json(self, url: str, params: dict[str, Any] | None = None, use_cache: bool = True) -> Any:
        path = self._cache_path(url, params) if use_cache else None
        if path and path.exists():
            return json.loads(path.read_text())
        data = self._get_json_uncached(url, params)
        if path is not None:
            path.write_text(json.dumps(data))
        return data

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_random_exponential(multiplier=1, max=30),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _get_json_uncached(self, url: str, params: dict[str, Any] | None) -> Any:
        self.limiter.wait()
        resp = self._client.get(url, params=params)
        if resp.status_code == 404:
            # openFDA returns 404 for "no matches"; treat as an empty result set.
            return {"results": [], "_not_found": True}
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_random_exponential(multiplier=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def download(self, url: str, dest: Path, overwrite: bool = False) -> tuple[Path, str]:
        """Stream a file to disk. Returns (path, sha256)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and not overwrite:
            return dest, sha256_file(dest)
        tmp = dest.with_suffix(dest.suffix + ".part")
        digest = hashlib.sha256()
        started = time.monotonic()
        with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for block in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(block)
                    digest.update(block)
        tmp.replace(dest)
        log_event(
            logger, "download_complete", url=url, path=str(dest),
            bytes=dest.stat().st_size, seconds=round(time.monotonic() - started, 1),
        )
        return dest, digest.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
