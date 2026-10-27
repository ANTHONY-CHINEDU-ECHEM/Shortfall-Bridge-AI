"""Scenario based clinical evaluation with property checks, latency and narrative grounding metrics."""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml

from ..domain.models import Recommendation, SubstitutionRequest
from ..engine.service import SubstitutionService


def _find(rec: Recommendation, ingredient: str, pool: str = "candidates"):
    items = rec.candidates if pool == "candidates" else rec.not_recommended
    return [c for c in items if c.ingredient == ingredient]


def check(rec: Recommendation, expect: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append({"check": name, "passed": bool(ok), "detail": detail})

    if "status_in" in expect:
        add("status_in", rec.status.value in expect["status_in"], f"status={rec.status.value}")
    for spec in expect.get("candidate_present", []):
        hits = _find(rec, spec["ingredient"])
        if spec.get("tier"):
            hits = [h for h in hits if h.tier.value == spec["tier"]]
        add("candidate_present", bool(hits), f"{spec}")
    for name in expect.get("not_allowed", []):
        add("not_allowed", not _find(rec, name), f"{name} absent from allowed options")
    for spec in expect.get("blocked", []):
        hits = _find(rec, spec["ingredient"], "blocked")
        ok = any(g.gate.value == spec["gate"] and g.status.value == "block" for h in hits for g in h.gates)
        add("blocked", ok, f"{spec}")
    for spec in expect.get("gate_status_in", []):
        hits = _find(rec, spec["ingredient"]) + _find(rec, spec["ingredient"], "blocked")
        statuses = [g.status.value for h in hits for g in h.gates if g.gate.value == spec["gate"]]
        add("gate_status_in", any(s in spec["statuses"] for s in statuses), f"{spec} observed={statuses}")
    for spec in expect.get("dose", []):
        hits = _find(rec, spec["ingredient"]) + _find(rec, spec["ingredient"], "blocked")
        convs = [h.dose_conversion for h in hits if h.dose_conversion]
        ok = False
        detail = "no conversion"
        for dc in convs:
            detail = f"feasible={dc.feasible} dose={dc.suggested_dose} method={dc.method}"
            cond = dc.feasible == spec["feasible"]
            if spec.get("contains"):
                cond = cond and spec["contains"] in (dc.suggested_dose or "")
            if spec.get("method_prefix"):
                cond = cond and dc.method.startswith(spec["method_prefix"])
            ok = ok or cond
        add("dose", ok, f"{spec['ingredient']}: {detail}")
    if "renal_source" in expect:
        src = rec.renal.source if rec.renal else "none"
        add("renal_source", src == expect["renal_source"], f"renal source={src}")
    for spec in expect.get("ranked_above", []):
        order = [c.ingredient for c in rec.candidates]
        hi, lo = spec["higher"], spec["lower"]
        ok = hi in order and (lo not in order or order.index(hi) < order.index(lo))
        add("ranked_above", ok, f"{hi} above {lo}; order={order}")
    for text in expect.get("warnings_contain", []):
        add("warnings_contain", any(text.lower() in w.lower() for w in rec.warnings), f"'{text}' in warnings")
    for spec in expect.get("gate_fact", []):
        hits = _find(rec, spec["ingredient"]) + _find(rec, spec["ingredient"], "blocked")
        values = [g.facts.get(spec["key"]) for h in hits for g in h.gates if g.gate.value == spec["gate"]]
        ok = any(spec["contains"] in (v if isinstance(v, list | str) else str(v)) for v in values if v is not None)
        add("gate_fact", ok, f"{spec} observed={values}")
    return results


def run_suite(service: SubstitutionService, scenarios_path: Path | None, out_dir: Path) -> dict[str, Any]:
    text = scenarios_path.read_text() if scenarios_path else (
        resources.files("shortfall_bridge.evaluation").joinpath("scenarios.yaml").read_text()
    )
    scenarios = yaml.safe_load(text)["scenarios"]
    rows, latencies = [], []
    gate_counts: Counter[str] = Counter()
    grounded_sentences = total_sentences = 0
    for sc in scenarios:
        req = SubstitutionRequest.model_validate(sc["request"])
        t0 = time.perf_counter()
        rec = service.recommend(req, audit=False)
        latencies.append((time.perf_counter() - t0) * 1000)
        checks = check(rec, sc.get("expect", {}))
        for c in rec.candidates + rec.not_recommended:
            for g in c.gates:
                gate_counts[f"{g.gate.value}:{g.status.value}"] += 1
        for s in rec.narrative.sentences:
            total_sentences += 1
            grounded_sentences += 1 if (s.citations or s.engine_facts) else 0
        rows.append({
            "id": sc["id"], "title": sc["title"], "status": rec.status.value,
            "candidates": [f"{c.tier.value}:{c.ingredient}" for c in rec.candidates],
            "blocked": [c.ingredient for c in rec.not_recommended],
            "checks": checks, "passed": all(c["passed"] for c in checks),
            "latency_ms": round(latencies[-1], 1), "narrative_generator": rec.narrative.generator,
        })
    n_checks = sum(len(r["checks"]) for r in rows)
    summary = {
        "scenarios": len(rows),
        "scenarios_passed": sum(r["passed"] for r in rows),
        "checks": n_checks,
        "checks_passed": sum(c["passed"] for r in rows for c in r["checks"]),
        "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else 0,
        "latency_ms_p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1) if latencies else 0,
        "narrative_sentences_grounded": f"{grounded_sentences}/{total_sentences}",
        "gate_outcomes": dict(sorted(gate_counts.items())),
        "run_at": datetime.now(UTC).isoformat(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evaluation.json").write_text(json.dumps({"summary": summary, "scenarios": rows}, indent=2))
    md = ["# Evaluation report", "", f"Run at {summary['run_at']}", "",
          f"Scenarios passed: {summary['scenarios_passed']} of {summary['scenarios']}",
          f"Checks passed: {summary['checks_passed']} of {summary['checks']}",
          f"Latency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms", "",
          "| Scenario | Status | Passed | Failing checks |", "|---|---|---|---|"]
    for r in rows:
        failing = "; ".join(c["detail"] for c in r["checks"] if not c["passed"]) or "none"
        md.append(f"| {r['id']} | {r['status']} | {'yes' if r['passed'] else 'no'} | {failing} |")
    (out_dir / "evaluation.md").write_text("\n".join(md) + "\n")
    (out_dir / "evaluation.junit.xml").write_text(junit_xml(rows))
    return {"summary": summary, "scenarios": rows}


def junit_xml(rows: list[dict[str, Any]]) -> str:
    """JUnit report so CI systems display each clinical scenario as a test case."""
    failures = sum(1 for r in rows if not r["passed"])
    cases = []
    for r in rows:
        body = ""
        if not r["passed"]:
            msg = "; ".join(c["detail"] for c in r["checks"] if not c["passed"])
            body = f'<failure message="{escape(msg, {chr(34): "&quot;"})}"/>'
        cases.append(f'  <testcase classname="clinical_scenarios" name="{escape(r["id"])}" '
                     f'time="{r["latency_ms"] / 1000:.3f}">{body}</testcase>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="shortfall_bridge_scenarios" '
            f'tests="{len(rows)}" failures="{failures}">\n' + "\n".join(cases) + "\n</testsuite>\n")
