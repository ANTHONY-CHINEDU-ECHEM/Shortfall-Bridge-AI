"""Minimal stand in for ``pydantic_settings`` used where that package is unavailable.

The browser runtime (Pyodide) ships pydantic but not pydantic-settings. This fallback reads
``SFB_`` prefixed environment variables into the same ``Settings`` model, parsing JSON for list
and dict fields, so configuration behaves the same everywhere. Explicit keyword arguments always
win over the environment, exactly as with pydantic-settings.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, ConfigDict


def SettingsConfigDict(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


class BaseSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    def __init__(self, **values: Any) -> None:
        cfg = getattr(type(self), "model_config", {}) or {}
        prefix = str(cfg.get("env_prefix", "")).upper()
        env: dict[str, Any] = {}
        for name in type(self).model_fields:
            raw = os.environ.get(prefix + name.upper())
            if raw is None or name in values:
                continue
            try:
                env[name] = json.loads(raw) if raw[:1] in "[{" else raw
            except json.JSONDecodeError:
                env[name] = raw
        super().__init__(**{**env, **values})
