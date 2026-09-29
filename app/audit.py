"""T10 — Signed audit registry (append-only, tamper-evident).

Each entry in ``data/audit.jsonl``::
    {seq, ts, kind, payload, prev_hash, hash, sig}

``hash = sha256(prev_hash + canonical_json({seq, ts, kind, payload}))``
(the INDEX contract hashes ``prev_hash + json(payload)``; we hash the full
body so that editing *any* field of a line breaks the chain — strictly
stronger, same structure). ``sig = HMAC-SHA256(key, hash)`` with the key
from env ``AUDIT_KEY`` (demo default, never use in production).

Kinds: model_update, promotion, rejection, freeze, override,
jidoka_approve, system1_decision (sampled).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path

AUDIT_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"
GENESIS_HASH = "0" * 64
DEMO_KEY = "demo-key-do-not-use-in-prod"


def _key() -> bytes:
    return os.environ.get("AUDIT_KEY", DEMO_KEY).encode()


def _canonical(seq: int, ts: str, kind: str, payload: dict) -> str:
    return json.dumps(
        {"seq": seq, "ts": ts, "kind": kind, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def _sig(entry_hash: str) -> str:
    return hmac.new(_key(), entry_hash.encode(), hashlib.sha256).hexdigest()


def _read_all(path: Path = AUDIT_PATH) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append(kind: str, payload: dict, path: Path = AUDIT_PATH) -> dict:
    """Append one entry to the chain and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = _read_all(path)
    seq = (entries[-1]["seq"] + 1) if entries else 1
    prev_hash = entries[-1]["hash"] if entries else GENESIS_HASH
    ts = datetime.now(timezone.utc).isoformat()
    canonical = _canonical(seq, ts, kind, payload)
    entry_hash = _hash(prev_hash, canonical)
    entry = {
        "seq": seq,
        "ts": ts,
        "kind": kind,
        "payload": payload,
        "prev_hash": prev_hash,
        "hash": entry_hash,
        "sig": _sig(entry_hash),
    }
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read(limit: int | None = None, path: Path = AUDIT_PATH) -> list[dict]:
    """Return up to ``limit`` most-recent entries, oldest-first."""
    entries = _read_all(path)
    if limit is not None:
        entries = entries[-limit:]
    return entries


def verify(path: Path = AUDIT_PATH) -> tuple[bool, int | None]:
    """Verify the chain. Returns (ok, first_bad_seq or None)."""
    prev = GENESIS_HASH
    expected_seq = 1
    for entry in _read_all(path):
        try:
            canonical = _canonical(entry["seq"], entry["ts"], entry["kind"], entry["payload"])
        except KeyError:
            return False, entry.get("seq")
        if entry["seq"] != expected_seq:
            return False, entry["seq"]
        if entry["prev_hash"] != prev:
            return False, entry["seq"]
        if entry["hash"] != _hash(prev, canonical):
            return False, entry["seq"]
        if entry["sig"] != _sig(entry["hash"]):
            return False, entry["seq"]
        prev = entry["hash"]
        expected_seq += 1
    return True, None
