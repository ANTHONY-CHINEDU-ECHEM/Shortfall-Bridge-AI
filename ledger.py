"""Tamper evident audit ledger.

Each audit record carries ``record_hash = sha256(prev_hash || canonical_json(record))``, so
altering or deleting any stored recommendation breaks every later hash. ``verify_chain`` walks
the records in order and reports the first break. This satisfies the integrity expectation that
DCB0129 style clinical risk management places on decision support audit trails without needing
external infrastructure; the head hash can additionally be anchored elsewhere (for example in a
release note or a write once bucket) for stronger guarantees.

Records written before hashing existed (``record_hash`` is null) are reported as legacy and the
chain starts after them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

GENESIS = "0" * 64


def canonical(request_id: str, request: dict[str, Any], response: dict[str, Any], created_at: str) -> str:
    return json.dumps(
        {"request_id": request_id, "created_at": created_at, "request": request, "response": response},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )


def chain_hash(prev_hash: str, request_id: str, request: dict[str, Any], response: dict[str, Any],
               created_at: str) -> str:
    return hashlib.sha256((prev_hash + canonical(request_id, request, response, created_at)).encode()).hexdigest()


def verify_chain(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """``records`` in sequence order, each with request_id, request, response, created_at, prev_hash, record_hash."""
    prev = GENESIS
    checked = legacy = 0
    for rec in records:
        if not rec.get("record_hash"):
            legacy += 1
            continue
        if rec.get("prev_hash") != prev:
            return {"ok": False, "checked": checked, "legacy_unchained": legacy, "first_break": rec["request_id"],
                    "reason": "prev_hash does not match the preceding record (a record was removed or reordered)"}
        expected = chain_hash(prev, rec["request_id"], rec["request"], rec["response"], rec["created_at"])
        if expected != rec["record_hash"]:
            return {"ok": False, "checked": checked, "legacy_unchained": legacy, "first_break": rec["request_id"],
                    "reason": "record content does not match its hash (the record was modified)"}
        prev = rec["record_hash"]
        checked += 1
    return {"ok": True, "checked": checked, "legacy_unchained": legacy, "head_hash": prev if checked else None}
