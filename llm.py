"""Language model providers behind one small interface (no vendor SDK dependency).

* ``openai_compatible``: any Chat Completions endpoint, including OpenRouter, Azure OpenAI
  compatible gateways, vLLM and Ollama.
* ``anthropic``: the Anthropic Messages API.
* ``none``: no model; the deterministic template narrative is used.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise LLMError("SFB_LLM_API_KEY is required for the openai_compatible provider")
        self.s = settings
        self.name = f"llm:{settings.llm_model}"

    def complete(self, system: str, user: str) -> str:
        resp = httpx.post(
            f"{self.s.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.s.llm_api_key}", "X-Title": "Shortfall Bridge AI"},
            json={
                "model": self.s.llm_model,
                "temperature": self.s.llm_temperature,
                "max_tokens": self.s.llm_max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=self.s.llm_timeout_s,
        )
        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]


class AnthropicClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise LLMError("SFB_LLM_API_KEY is required for the anthropic provider")
        self.s = settings
        self.name = f"llm:{settings.llm_model}"

    def complete(self, system: str, user: str) -> str:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.s.llm_api_key or "", "anthropic-version": "2023-06-01"},
            json={
                "model": self.s.llm_model,
                "max_tokens": self.s.llm_max_tokens,
                "temperature": self.s.llm_temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.s.llm_timeout_s,
        )
        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        return "".join(b.get("text", "") for b in resp.json().get("content", []))


def build_llm(settings: Settings) -> LLMClient | None:
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "anthropic":
        return AnthropicClient(settings)
    return OpenAICompatibleClient(settings)
