"""T07 done-checks: residual GBM + conformal intervals + metrics."""

import csv
import json

import numpy as np

from app.ontology import build, ingest
from app.system2 import (
    attach_current_state,
    backbone,
    load_residual,
    metrics,
    predict_full,
    residual_correct,
    train_residual,
    trains_from_rows,
)


def _setup(now_min=600.0):
    net = json.load(open("data/network.json"))
    rows = list(csv.DictReader(open("data/trains.csv")))
    G = build(net, rows)
    for ev in csv.DictReader(open("data/events.csv")):
        try:
            ts = float(ev["ts"])
        except (TypeError, ValueError):
            continue
        if ts <= now_min and ev["train_id"] and ev["station"]:
            ingest(G, {**ev, "ts": ts})
    trains = trains_from_rows(rows)
    attach_current_state(trains, G)
    return G, trains


def test_train_and_metrics():
    out = train_residual(verbose=False)
    m = out["metrics"]
    assert {"next", "plus2", "dest"} <= set(m)
    for h in ("next", "plus2", "dest"):
        d = m[h]
        assert d["mae_model"] < 6.0
        assert 0.5 <= d["coverage"] <= 1.0
        # Residual median must improve on the raw backbone point forecast
        # (backbone error on test rows = mean|target| <= ~3.9; margin is wide).
        assert d["mae_model"] < 3.9
    printed = metrics()
    assert printed == m


def test_predict_full_ordering_and_drivers():
    G, trains = _setup()
    t = next(t for t in trains if 0 <= t["cur_seq"] <= 8)
    probs = {"grows": 0.7, "stable": 0.2, "recovers": 0.1}
    full = predict_full(t, 600.0, G, probs, 0.7)
    assert len(full) == 12
    bb = {p["seq"]: p for p in backbone(t, 600.0, G)}
    for p in full:
        assert 0.0 <= p["p_on_time"] <= 1.0
        if p["seq"] > t["cur_seq"]:
            assert p["delay_p10"] <= p["delay_p50"] <= p["delay_p90"]
            # drivers.learned is the median residual correction.
            assert abs(p["drivers"]["learned"] - (p["delay_p50"] - bb[p["seq"]]["delay"])) < 0.2


def test_confidence_widens_band_and_ontime_sane():
    load_residual()
    feat = [2.0, 14.0, 2.0, 0.0, 1.0, 0, 10.0, 0.0,
            0.6, 0.3, 0.1, 1.0, 2.0]
    hi = residual_correct(2.0, feat, 0.9)
    lo = residual_correct(2.0, feat, 0.3)
    assert (lo["p90"] - lo["p10"]) > (hi["p90"] - hi["p10"])
    late = residual_correct(30.0, [30.0] + feat[1:], 0.8)
    early = residual_correct(-5.0, [-5.0] + feat[1:], 0.8)
    assert late["p_on_time"] < early["p_on_time"]
    assert late["p_on_time"] < 0.5 < early["p_on_time"]
