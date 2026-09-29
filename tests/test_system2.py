"""T06 done-checks: backbone ETAs, NaN-free matrix, what-if shift."""

import csv
import json
import math

from app.ontology import build, ingest
from app.system2 import (
    attach_current_state,
    backbone,
    delay_matrix,
    trains_from_rows,
    whatif,
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
    return net, G, trains


def test_backbone_all_trains():
    _, G, trains = _setup()
    assert len(trains) == 40
    for t in trains:
        preds = backbone(t, 600.0, G)
        assert len(preds) == 12
        for p in preds:
            assert {"station", "seq", "pred_arr", "pred_dep", "delay",
                    "drivers", "flags"} <= set(p)
            assert set(p["drivers"]) == {"run", "dwell", "restriction",
                                         "recovery", "learned"}


def test_matrix_no_nans():
    _, G, trains = _setup()
    m = delay_matrix(600.0, trains, G)
    assert len(m["stations"]) == 12 and len(m["trains"]) == 40
    assert len(m["values"]) == 12 and all(len(r) == 40 for r in m["values"])
    for row in m["values"]:
        for v in row:
            assert isinstance(v, float) and not math.isnan(v)


def test_restriction_driver_applies():
    net, G, trains = _setup()
    r = net["restrictions"][0]
    frm, to = r["section"].split("-")
    t = next(t for t in trains if t["cur_seq"] >= 0)
    t = {**t, "cur_seq": next(i for i, s in enumerate(t["stops"]) if s["station"] == frm),
         "cur_delay": 2.0, "cur_ts": r["start_min"] + 10}
    preds = backbone(t, 600.0, G)
    p = next(p for p in preds if p["station"] == to)
    assert p["drivers"]["restriction"] == r["extra_min"]
    assert "restriction" in p["flags"]


def test_whatif_shifts_downstream():
    _, G, trains = _setup()
    t = next(t for t in trains if 0 <= t["cur_seq"] <= 8)
    hold_station = t["stops"][t["cur_seq"] + 1]["station"]
    w = whatif(t["train_id"], 10.0, hold_station, trains, G, 600.0)
    assert w["label"] == "scenario"
    assert w["diffs"][0]["delta_min"] == 0.0  # arrival at hold station unchanged
    for d in w["diffs"][1:]:
        assert d["delta_min"] > 0  # departures/arrivals downstream shift later
