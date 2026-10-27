"""Runtime configuration, loaded from environment variables prefixed with ``SFB_``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SFB_", env_file=".env", extra="ignore")

    # Environment
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # Storage
    store_backend: Literal["postgres", "memory"] = "postgres"
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
    def effective_alt_class_min_similarity(self) -> float:
        if self.alt_class_min_similarity is not None:
            return self.alt_class_min_similarity
        return 0.45 if self.embedding_backend == "hashing" else 0.78

    # API
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"])
    api_rate_limit_per_minute: int = 120


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
