"""The engine must import and run without server only dependencies (the Pyodide browser runtime).

A child interpreter blocks httpx, tenacity, prometheus_client, pydantic_settings, fastapi,
starlette, sqlalchemy, psycopg and lxml, then runs a full live store recommendation against the
openFDA emulator. If any of those modules creeps back into the engine import path, this fails.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

BLOCKED = ["httpx", "tenacity", "prometheus_client", "pydantic_settings", "fastapi", "starlette", "sqlalchemy",
           "psycopg", "lxml", "uvicorn", "typer"]


def test_engine_runs_without_server_dependencies() -> None:
    backend = Path(__file__).resolve().parents[1]
    code = textwrap.dedent(f"""
        import importlib.abc, sys, json
        BLOCKED = {BLOCKED!r}
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError(f"blocked for portability test: {{name}}")
                return None
        sys.meta_path.insert(0, Block())
        sys.path.insert(0, {str(backend)!r})
        sys.path.insert(0, {str(backend / "tests")!r})
        from openfda_emulator import EmulatorFetcher
        from shortfall_bridge.api.container import Container
        from shortfall_bridge.api.dispatch import dispatch
        from shortfall_bridge.api.handlers import ApiHandlers
        from shortfall_bridge.browser.runtime import BrowserFetcher, BrowserRuntime, arun_plan
        from shortfall_bridge.config import Settings
        from shortfall_bridge.embeddings import HashingEmbedder
        from shortfall_bridge.store.live import LiveOpenFDAStore
        s = Settings(store_backend="live", embedding_backend="hashing", llm_provider="none", log_json=False)
        store = LiveOpenFDAStore(EmulatorFetcher(), HashingEmbedder(), s)
        h = ApiHandlers(Container.build(s, store=store, embedder=HashingEmbedder(), online_resolution=False), "browser")
        out = json.loads(dispatch(h, "POST", "/api/v1/substitutions",
                                  {{"drug": "furosemide", "route": "intravenous", "current_dose": {{"amount": 40}}}}).to_json())
        meta = json.loads(dispatch(h, "GET", "/api/v1/meta").to_json())["body"]
        print(json.dumps({{"status": out["status"], "candidates": [c["ingredient"] for c in out["body"]["candidates"]],
                          "runtime": meta["runtime"], "loaded": sorted(m for m in BLOCKED if m in sys.modules)}}))
    """)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["status"] == 200 and "bumetanide" in out["candidates"] and out["runtime"] == "browser"
    assert out["loaded"] == []


def test_settings_fallback_reads_environment(monkeypatch) -> None:
    from pydantic import Field

    from shortfall_bridge._settings_fallback import BaseSettings, SettingsConfigDict

    class S(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="T_")
        a: int = 1
        b: list[str] = Field(default_factory=list)
        c: str = "x"

    monkeypatch.setenv("T_A", "5")
    monkeypatch.setenv("T_B", '["p", "q"]')
    s = S(c="explicit")
    assert s.a == 5 and s.b == ["p", "q"] and s.c == "explicit"
