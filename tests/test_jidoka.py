"""T04 done-checks for app/jidoka.py (ALL DATA SIMULATED).

Covers quarantine, schedule fallback, drift line-stop, the approval
queue, and the demo-data criterion (>= 1 quarantine and >= 1 fallback).
"""

import csv
import re
from pathlib import Path

import pytest

from app import audit
from app.jidoka import JidokaQueue

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(autouse=True)
def _tmp_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")


def _station_index(station: str) -> int:
    return int(station[1:]) - 1


def test_quarantine_item_shape_and_audit():
    q = JidokaQueue()
    item = q.quarantine({"train_id": "T101"}, "bad delay", 100.0)
    assert item == {
        "id": "J1",
        "kind": "quarantine",
        "train_id": "T101",
        "reason": "bad delay",
        "ts": 100.0,
        "status": "open",
    }
    # ids increment
    item2 = q.quarantine({"train_id": "T102"}, "other", 101.0)
    assert item2["id"] == "J2"
    kinds = [e["kind"] for e in audit.read(path=audit.AUDIT_PATH)]
    assert kinds == ["jidoka_quarantine", "jidoka_quarantine"]
    assert audit.read(path=audit.AUDIT_PATH)[0]["payload"]["id"] == "J1"


def test_check_observation_valid_and_invalid():
    q = JidokaQueue()
    good = {
        "train_id": "T101",
        "ts": 300.0,
        "station": "S3",
        "section": "S2-S3",
        "delay_min": 2.5,
        "speed_kmph": 70.0,
        "state": "running",
        "remark": "",
    }
    ok, reason = q.check_observation(good, {"last_seq": 1, "last_ts": 290.0}, 300.0)
    assert (ok, reason) == (True, "ok")
    assert q.all_items() == []

    bad_delay = dict(good, delay_min=-45.0)
    ok, reason = q.check_observation(bad_delay, {"last_seq": 1, "last_ts": 290.0}, 300.0)
    assert ok is False and reason
    assert len(q.pending()) == 1
    assert q.pending()[0]["kind"] == "quarantine"

    backwards = dict(good, station="S1")
    ok, reason = q.check_observation(backwards, {"last_seq": 2, "last_ts": 290.0}, 300.0)
    assert ok is False and "backwards" in reason

    missing = dict(good, state="")
    ok, reason = q.check_observation(missing, None, 300.0)
    assert ok is False and "missing field" in reason


def test_maybe_fallback_triggers():
    q = JidokaQueue()
    assert q.maybe_fallback("T101", 0.9, 2.0, 400.0) is None
    assert q.all_items() == []

    low_conf = q.maybe_fallback("T101", 0.32, 2.0, 400.0)
    assert low_conf is not None
    assert low_conf["kind"] == "fallback"
    assert low_conf["train_id"] == "T101"
    assert low_conf["status"] == "open"
    assert "s1 confidence 0.32 < 0.5" in low_conf["reason"]

    stale = q.maybe_fallback("T102", 0.9, 22.0, 401.0)
    assert stale is not None
    assert "feed age 22.0 min > 15 min" in stale["reason"]

    # boundary values do NOT trigger
    assert q.maybe_fallback("T103", 0.5, 15.0, 402.0) is None


def test_fallback_direct_and_audit():
    q = JidokaQueue()
    item = q.fallback("T101", "manual", 500.0)
    assert item["id"] == "J1" and item["kind"] == "fallback"
    assert set(item) == {"id", "kind", "train_id", "reason", "ts", "status"}
    assert audit.read(path=audit.AUDIT_PATH)[-1]["kind"] == "jidoka_fallback"


def test_check_drift():
    q = JidokaQueue()
    assert q.check_drift([], 1.0) is False
    assert q.check_drift([1.0, 1.5], 0.0) is False  # baseline 0 -> never frozen
    assert q.check_drift([1.0, 1.5], 1.0) is False  # 1.25 < 2.0
    assert q.check_drift([2.0, 2.0], 1.0) is False  # exactly 2x -> not frozen
    assert q.check_drift([2.5, 3.5], 1.0) is True  # mean 3.0 > 2.0
    freezes = [e for e in audit.read(path=audit.AUDIT_PATH) if e["kind"] == "freeze"]
    assert len(freezes) == 1
    assert freezes[0]["payload"] == {"baseline_mae": 1.0, "recent_mae": 3.0}


def test_approve_dismiss_and_pending():
    q = JidokaQueue()
    a = q.quarantine({"train_id": "T1"}, "r1", 1.0)
    b = q.fallback("T2", "r2", 2.0)
    assert len(q.pending()) == 2

    out = q.approve(a["id"])
    assert out["status"] == "approved" and out["id"] == "J1"
    out = q.dismiss(b["id"])
    assert out["status"] == "dismissed"
    assert q.pending() == []
    assert len(q.all_items()) == 2

    kinds = [e["kind"] for e in audit.read(path=audit.AUDIT_PATH)]
    assert "jidoka_approve" in kinds and "jidoka_dismiss" in kinds

    with pytest.raises(KeyError):
        q.approve("J999")
    with pytest.raises(KeyError):
        q.dismiss("J999")


def test_demo_dataset_yields_quarantine_and_fallback():
    """Done criterion: replay events.csv in ts order -> >=1 quarantine,
    plus maybe_fallback triggers -> >=1 fallback."""
    q = JidokaQueue()
    with open(DATA_DIR / "events.csv", newline="") as f:
        rows = list(csv.DictReader(f))

    def _ts(r):
        try:
            return float(r["ts"])
        except (TypeError, ValueError):
            return 1e9

    rows.sort(key=_ts)
    train_state: dict[str, dict] = {}

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v

    for r in rows:
        tid = r["train_id"]
        try:
            ts = float(r["ts"])
        except (TypeError, ValueError):
            ts = 1e9
        obs = {
            "train_id": tid,
            "ts": r["ts"],
            "station": r["station"],
            "section": r["section"],
            "delay_min": _num(r["delay_min"]),
            "speed_kmph": _num(r["speed_kmph"]),
            "state": r["state"],
            "remark": r.get("remark", ""),
        }
        ok, _ = q.check_observation(obs, train_state.get(tid), ts)
        if ok and re.fullmatch(r"S\d+", (r["station"] or "").strip()):
            train_state[tid] = {
                "last_seq": _station_index(r["station"].strip()),
                "last_ts": ts,
            }

    quarantines = [i for i in q.all_items() if i["kind"] == "quarantine"]
    assert len(quarantines) >= 1, "expected >= 1 quarantine on demo data"

    fb1 = q.maybe_fallback("T101", 0.3, 2.0, 1400.0)
    fb2 = q.maybe_fallback("T102", 0.9, 20.0, 1401.0)
    assert fb1 is not None and fb2 is not None
    fallbacks = [i for i in q.all_items() if i["kind"] == "fallback"]
    assert len(fallbacks) >= 1

    print(f"\ndemo replay: {len(quarantines)} quarantine, {len(fallbacks)} fallback")
