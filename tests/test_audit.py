"""T10 done-check: tampering with one line must fail verify() at that seq."""

import json

from app import audit


def test_audit_chain_and_tamper(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit.append("model_update", {"version": "v1"}, path=path)
    audit.append("jidoka_approve", {"id": "J1"}, path=path)
    e3 = audit.append("promotion", {"version": "v2"}, path=path)
    assert e3["seq"] == 3

    ok, bad = audit.verify(path=path)
    assert (ok, bad) == (True, None)

    entries = audit.read(path=path)
    assert len(entries) == 3
    assert audit.read(limit=2, path=path)[0]["seq"] == 2

    # Tamper with line 2 (payload edit) -> verify fails at seq 2.
    lines = path.read_text().splitlines()
    obj = json.loads(lines[1])
    obj["payload"] = {"id": "HACKED"}
    lines[1] = json.dumps(obj)
    path.write_text("\n".join(lines) + "\n")

    ok, bad = audit.verify(path=path)
    assert ok is False and bad == 2
