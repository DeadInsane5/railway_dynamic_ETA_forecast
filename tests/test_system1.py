"""T05 done-checks: ask() calibration interface, read_remark, train() ECE, logging."""

import app.system1 as s1
from app import audit


def _state(**kw):
    base = {
        "train_id": "T101",
        "delay_history": [1.0, 2.5, 2.0, 4.5],
        "speed_kmph": 60.0,
        "restriction_ahead": True,
        "upstream_delay": 3.0,
    }
    base.update(kw)
    return base


def test_ask_regime_calibrated():
    # ask() must work without an explicit train() call (lazy-trains).
    out = s1.ask(_state(), s1.REGIME_QUESTION, list(s1.REGIMES))
    probs = out["probs"]
    assert set(probs) == set(s1.REGIMES)
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert all(0.0 <= p <= 1.0 for p in probs.values())
    assert out["confidence"] == max(probs.values())
    assert out["latency_ms"] >= 0.0


def test_ask_unknown_question_uniform():
    out = s1.ask(_state(), "will it rain tomorrow?", ["yes", "no", "maybe"])
    assert out["probs"] == {"yes": 1 / 3, "no": 1 / 3, "maybe": 1 / 3}
    assert out["confidence"] == 1 / 3


def test_ask_odd_inputs_never_raise():
    out = s1.ask({}, s1.REGIME_QUESTION, [])  # empty options
    assert out["probs"] == {} and out["confidence"] == 0.0
    out = s1.ask(None, None, None)  # all odd
    assert out["probs"] == {} and out["confidence"] == 0.0
    out = s1.ask(_state(delay_history="junk", speed_kmph="fast"), s1.REGIME_QUESTION, list(s1.REGIMES))
    assert abs(sum(out["probs"].values()) - 1.0) < 1e-6


def test_read_remark_keywords():
    assert s1.read_remark("Signal Failure near S5, trains piloted") == {"risk": "signal", "severity": "high"}
    assert s1.read_remark("DENSE FOG near S8") == {"risk": "weather", "severity": "medium"}
    assert s1.read_remark("Track maintenance at S3") == {"risk": "asset", "severity": "medium"}
    assert s1.read_remark("") == {"risk": "none", "severity": "low"}
    assert s1.read_remark("all clear, running on time") == {"risk": "none", "severity": "low"}


def test_train_metrics():
    m = s1.train(verbose=True)  # prints ECE before/after + test acc
    assert {"ece_before", "ece_after", "test_acc", "n_train", "n_val", "n_test"} <= set(m)
    assert m["ece_after"] <= m["ece_before"] + 0.02
    assert 0.0 <= m["test_acc"] <= 1.0
    assert m["n_train"] > 0 and m["n_val"] > 0 and m["n_test"] > 0


def test_calls_log_grows_and_audits(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    n0 = len(s1.calls)
    s1.ask(_state(), s1.REGIME_QUESTION, list(s1.REGIMES))
    assert len(s1.calls) == n0 + 1
    entry = s1.calls[-1]
    assert {"ts", "question", "options", "probs", "confidence"} <= set(entry)
    # Drive the call count to the next multiple of 20 -> sampled audit entry.
    target = (len(s1.calls) // 20 + 1) * 20
    for _ in range(target - len(s1.calls)):
        s1.ask(_state(), s1.REGIME_QUESTION, list(s1.REGIMES))
    assert len(s1.calls) == target and len(s1.calls) % 20 == 0
    entries = audit.read(path=tmp_path / "audit.jsonl")
    assert entries and entries[-1]["kind"] == "system1_decision"
