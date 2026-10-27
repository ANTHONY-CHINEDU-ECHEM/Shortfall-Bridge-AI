"""Runtime configuration, loaded from environment variables prefixed with ``SFB_``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - exercised by the browser runtime and minimal installs
    from ._settings_fallback import BaseSettings, SettingsConfigDict  # type: ignore[assignment]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SFB_", env_file=".env", extra="ignore")

    # Environment
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # Storage
    store_backend: Literal["postgres", "memory", "live"] = "postgres"
    database_url: str = "postgresql+psycopg://shortfall:shortfall@localhost:5432/shortfall"
    data_dir: Path = Path("./data")
    snapshot_path: Path | None = None  # memory backend: JSONL snapshot to load on startup

    # Upstream sources (all public, no credentials required)
    openfda_base_url: str = "https://api.fda.gov"
    openfda_api_key: str | None = None  # optional, raises openFDA rate limits
    openfda_manifest_url: str = "https://api.fda.gov/download.json"
    rxnav_base_url: str = "https://rxnav.nlm.nih.gov/REST"
    dailymed_base_url: str = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
    orange_book_url: str = "https://www.fda.gov/media/76860/download?attachment"
    http_timeout_s: float = 60.0
    http_max_retries: int = 5
    rxnav_requests_per_second: float = 15.0  # NLM asks clients to stay under 20 rps

    # Live openFDA store (SFB_STORE_BACKEND=live): no ingestion, data fetched per request and cached
    live_label_limit: int = Field(default=8, ge=1, le=100)  # labels fetched per ingredient
    live_max_class_members: int = Field(default=8, ge=0, le=40)  # same class alternatives hydrated per class
    live_max_partners: int = Field(default=8, ge=0, le=20)  # equivalence table partners hydrated
    live_label_ttl_s: float = 7 * 24 * 3600.0
    live_shortage_ttl_s: float = 6 * 3600.0
    live_max_workers: int = Field(default=6, ge=1, le=16)

    # Embeddings
    embedding_backend: Literal["sentence_transformers", "hashing"] = "sentence_transformers"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_batch_size: int = 64

    # Retrieval
    chunk_target_tokens: int = 220
    chunk_overlap_tokens: int = 40
    retrieval_top_k: int = 8
    rrf_k: int = 60
    reranker_model: str | None = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_enabled: bool = False

    # Generation
    llm_provider: Literal["none", "openai_compatible", "anthropic"] = "none"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str | None = None
    llm_model: str = "anthropic/claude-sonnet-4.5"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1200
    llm_timeout_s: float = 60.0

    # Engine
    max_candidates: int = 12
    max_class_members: int = 40
    # Minimum cosine similarity between indication passages for a Tier 4 (alternative class) proposal.
    # None selects a calibrated default per embedding backend; tune with `shortfall evaluate`.
    alt_class_min_similarity: float | None = None

    @property
    def api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

    @property
    def effective_alt_class_min_similarity(self) -> float:
        if self.alt_class_min_similarity is not None:
            return self.alt_class_min_similarity
        return 0.45 if self.embedding_backend == "hashing" else 0.78

    # API
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"])
    api_rate_limit_per_minute: int = 120
    # Comma separated accepted API keys. Empty disables authentication (development only).
    api_keys: str = ""
    # Use X-Forwarded-For for rate limiting when the API sits behind a trusted reverse proxy (nginx in compose).
    trust_proxy_headers: bool = False
    max_body_bytes: int = 512 * 1024
    public_app_url: str | None = None  # used in CDS Hooks card links back to the workbench
    cds_always_card: bool = False  # also return an info card when the ordered drug is not in shortage


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
