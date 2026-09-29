"""T06 — System 2 backbone + delay matrix (ALL DATA SIMULATED).

Event-driven backbone with additive terms per section::

    arrival[i+1]   = departure[i] + section_runtime + restriction_extra - recovery_slack
    departure[i+1] = max(arrival[i+1] + dwell, sched_dep[i+1] + inherited_rake_delay)

- ``restriction_extra``: sum of ``extra_min`` of restrictions active on the
  section at traversal time (from ontology). The sim's speed restrictions are
  emitted as integrated extra minutes, i.e. the time integral of
  ``km/v_restricted - km/v_normal`` — the backbone consumes that integral
  directly instead of re-deriving it from an assumed caution speed.
- ``recovery_slack`` = min(SECTION_SLACK, 0.5 * current_delay) for late
  trains, 0 otherwise.
- Congestion: if the latest observed entry into the next section is less
  than HEADWAY_MIN before ours, add the headway conflict wait (mirrors sim).
- ``drivers`` per station follow the INDEX contract:
  {run, dwell, restriction, recovery, learned} (learned = 0.0 here; T07 fills it).

Train-dict shape (built by ``trains_from_rows`` / used by T09 engine)::

    {"train_id", "name", "rake_id",
     "stops": [{"station", "sched_arr", "sched_dep"}],
     "cur_seq": int   # last observed station index, -1 = nothing observed yet
     "cur_delay": float, "cur_ts": float}
"""

from __future__ import annotations

import networkx as nx

from app.ontology import restrictions_on, upstream_delay

HEADWAY_MIN = 6.0
DWELL_MIN = 2.0
JUNCTION_DWELL_MIN = 3.0
SECTION_SLACK = 1.5  # scheduled recovery margin per section (matches sim)


# ---------------------------------------------------------------- helpers

def _stations_in_order(G: nx.DiGraph) -> list[str]:
    idx = G.graph["station_index"]
    return sorted(idx, key=idx.get)


def _sections_in_order(G: nx.DiGraph) -> list[dict]:
    """Section dicts ordered along the corridor (section i: station i -> i+1)."""
    stations = _stations_in_order(G)
    by_leg = {}
    for n, d in G.nodes(data=True):
        if d.get("kind") == "Section":
            by_leg[(d["from"], d["to"])] = {"id": n, **d}
    return [by_leg[(stations[i], stations[i + 1])] for i in range(len(stations) - 1)]


def _dwell_for(section_type: str) -> float:
    return JUNCTION_DWELL_MIN if section_type == "junction" else DWELL_MIN


def _congestion_wait(
    G: nx.DiGraph, section_id: str, entry_ts: float, cap_ts: float | None = None
) -> float:
    """Headway wait from the latest observed entry into this section.

    Only entries with ts <= min(entry_ts, cap_ts) count. At runtime cap_ts is
    the engine clock (all ingested obs are older anyway); in offline training
    cap_ts is the as-of time so future observations never leak in.
    """
    latest = None
    cap = entry_ts if cap_ts is None else min(entry_ts, cap_ts)
    for _, d in G.nodes(data=True):
        if d.get("kind") == "Event" and d.get("section") == section_id:
            try:
                ts = float(d.get("ts"))
            except (TypeError, ValueError):
                continue
            if ts <= cap and (latest is None or ts > latest):
                latest = ts
    if latest is None:
        return 0.0
    return max(0.0, HEADWAY_MIN - (cap - latest))


def trains_from_rows(rows: list[dict]) -> list[dict]:
    """Group trains.csv row-dicts into train dicts (schedule only)."""
    by_train: dict[str, list[dict]] = {}
    for r in rows:
        by_train.setdefault(r["train_id"], []).append(r)
    trains = []
    for tid, rs in by_train.items():
        rs = sorted(rs, key=lambda r: int(r["seq"]))
        trains.append(
            {
                "train_id": tid,
                "name": rs[0]["name"],
                "rake_id": rs[0]["rake_id"],
                "stops": [
                    {
                        "station": r["station"],
                        "sched_arr": float(r["sched_arr"]),
                        "sched_dep": float(r["sched_dep"]),
                    }
                    for r in rs
                ],
                "cur_seq": -1,
                "cur_delay": 0.0,
                "cur_ts": 0.0,
            }
        )
    trains.sort(key=lambda t: t["stops"][0]["sched_dep"])
    return trains


def attach_current_state(trains: list[dict], G: nx.DiGraph) -> None:
    """Fill cur_seq/cur_delay/cur_ts in place from ontology Train nodes."""
    for t in trains:
        node = G.nodes.get(t["train_id"], {})
        seq = node.get("last_seq")
        t["cur_seq"] = seq if seq is not None else -1
        t["cur_delay"] = float(node.get("last_delay", 0.0) or 0.0)
        try:
            t["cur_ts"] = float(node.get("last_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            t["cur_ts"] = 0.0


# ---------------------------------------------------------------- backbone

def backbone(
    train: dict,
    now_min: float,
    G: nx.DiGraph,
    hold: dict | None = None,
) -> list[dict]:
    """Point-prediction ETAs for every station of one train.

    ``hold`` (for what-if): {"station": str, "minutes": float} — extra dwell
    added to the departure from that station. Returns per-station dicts with
    keys: station, seq, sched_arr, sched_dep, pred_arr, pred_dep, delay
    (predicted arrival delay), drivers {run, dwell, restriction, recovery,
    learned}, flags [].
    """
    stations = _stations_in_order(G)
    sections = _sections_in_order(G)
    stops = train["stops"]
    inherited = upstream_delay(G, train["train_id"])

    cur_seq = train.get("cur_seq", -1)
    cur_delay = train.get("cur_delay", 0.0)
    cur_ts = train.get("cur_ts", 0.0) or 0.0

    out: list[dict] = []
    # Passed stations: backfill current delay, flagged (no per-station actuals
    # available from the observation stream — only the last observation).
    for i in range(0, min(cur_seq + 1, len(stops))):
        s = stops[i]
        out.append(
            {
                "station": s["station"], "seq": i,
                "sched_arr": s["sched_arr"], "sched_dep": s["sched_dep"],
                "pred_arr": round(s["sched_arr"] + cur_delay, 1),
                "pred_dep": round(s["sched_dep"] + cur_delay, 1),
                "delay": round(cur_delay, 1),
                "drivers": {"run": 0.0, "dwell": 0.0, "restriction": 0.0,
                            "recovery": 0.0, "learned": 0.0},
                "flags": ["observed"] if i == cur_seq else ["backfilled"],
            }
        )

    # Forward simulation from the last known point.
    if cur_seq >= 0:
        dep_time = max(cur_ts, stops[cur_seq]["sched_dep"] + cur_delay)
        delay = cur_delay
        start = cur_seq + 1
    else:
        dep_time = stops[0]["sched_dep"]
        delay = cur_delay  # 0.0 unless preset
        # Origin station itself.
        out.append(
            {
                "station": stops[0]["station"], "seq": 0,
                "sched_arr": stops[0]["sched_arr"],
                "sched_dep": stops[0]["sched_dep"],
                "pred_arr": round(dep_time, 1), "pred_dep": round(dep_time, 1),
                "delay": round(delay, 1),
                "drivers": {"run": 0.0, "dwell": 0.0, "restriction": 0.0,
                            "recovery": 0.0, "learned": 0.0},
                "flags": ["origin"],
            }
        )
        start = 1

    hold_seq = None
    if hold is not None:
        hold_seq = next((i for i, s in enumerate(stops) if s["station"] == hold["station"]), None)
        if hold_seq is None:
            raise ValueError(f"unknown hold station: {hold['station']}")
        if hold_seq <= cur_seq:
            raise ValueError(f"hold station {hold['station']} already passed")

    for i in range(start, len(stops)):
        sec = sections[i - 1]
        run = sec["sched_run_min"]
        wait = _congestion_wait(G, sec["id"], dep_time, cap_ts=now_min)
        restriction = sum(
            r.get("extra_min", 0.0) for r in restrictions_on(G, sec["id"], dep_time)
        )
        recovery = min(SECTION_SLACK, 0.5 * max(0.0, delay))
        arr = dep_time + run + wait + restriction - recovery
        dwell = _dwell_for(sec["type"])
        extra_hold = hold["minutes"] if hold is not None and i == hold_seq else 0.0
        dep = max(arr + extra_hold, stops[i]["sched_dep"] + inherited)
        # Carry departure delay forward (same convention as sim).
        delay = dep - stops[i]["sched_dep"]

        flags = []
        if wait > 0:
            flags.append("congested")
        if restriction > 0:
            flags.append("restriction")
        if hold is not None and i == hold_seq:
            flags.append("scenario_hold")

        out.append(
            {
                "station": stops[i]["station"], "seq": i,
                "sched_arr": stops[i]["sched_arr"],
                "sched_dep": stops[i]["sched_dep"],
                "pred_arr": round(arr, 1), "pred_dep": round(dep, 1),
                "delay": round(dep - stops[i]["sched_dep"], 1),
                "drivers": {
                    "run": round(run + wait, 1),
                    "dwell": round(dwell + extra_hold, 1),
                    "restriction": round(restriction, 1),
                    "recovery": round(recovery, 1),
                    "learned": 0.0,
                },
                "flags": flags,
            }
        )
        dep_time = dep

    return out


def baseline(train: dict) -> list[dict]:
    """Baseline 1 (T07 comparison): schedule + current delay, flat.

    Returns per-station {"station", "seq", "delay"} with delay = cur_delay.
    """
    d = round(train.get("cur_delay", 0.0), 1)
    return [{"station": s["station"], "seq": i, "delay": d}
            for i, s in enumerate(train["stops"])]


# ---------------------------------------------------------------- matrix / what-if

# ================================================================ T07 residual
# Residual quantile GBM + split-conformal (ALL DATA SIMULATED).
#
# Target: actual arrival delay at a future station minus the backbone point
# prediction (residual). Final per-station output: p10/p50/p90 delay plus
# p_on_time (delay <= 5 min) by interpolating the quantiles. The median
# correction is reported as drivers["learned"]; interval half-widths are
# widened by 1 + (1 - confidence) from System 1. SHAP skipped — gradient
# boosting feature importances are reported instead.

import csv
import json
import pickle
from pathlib import Path

import numpy as np

RESID_FEATURES = [
    "bb_delay", "run", "dwell", "restriction", "recovery",
    "congestion", "hour", "section_type",
    "p_grows", "p_stable", "p_recovers",
    "horizon", "delay_k",
]
RESID_ALPHAS = (0.1, 0.5, 0.9)
RESID_PATH = Path(__file__).resolve().parent.parent / "data" / "residual.pkl"
SECTION_TYPE_ORD = {"plain": 0.0, "loop": 1.0, "junction": 2.0}
ONTIME_THRESHOLD_MIN = 5.0
HORIZONS = ("next", "plus2", "dest")  # j-k==1, j-k==2, j==S12

_resid_bundle = None


def _horizon_bucket(k: int, j: int, n_stations: int) -> str:
    if j - k == 1:
        return "next"
    if j - k == 2:
        return "plus2"
    if j == n_stations - 1:
        return "dest"
    return "mid"


def build_residual_dataset() -> list[dict]:
    """Offline training rows: one per (train, as-of k, future j).

    Each row holds backbone terms at j, schedule-based congestion, arrival
    hour, section type, System 1 regime probs at k, horizon, delay at k, and
    the residual target (actual_j - backbone_j). Uses full ground truth —
    training-only; the runtime path (predict_full) uses observations.
    """
    from app import system1 as _s1

    data_dir = Path(__file__).resolve().parent.parent / "data"
    net = json.loads((data_dir / "network.json").read_text())
    rows = list(csv.DictReader(open(data_dir / "trains.csv")))
    events = list(csv.DictReader(open(data_dir / "events.csv")))

    from app.ontology import build as _build, ingest as _ingest
    G = _build(net, rows)
    for ev in events:
        try:
            ts = float(ev["ts"])
        except (TypeError, ValueError):
            continue
        if ev["train_id"] and ev["station"]:
            _ingest(G, {**ev, "ts": ts})

    # Speed join: (train, station) -> [(ts, speed)] for System 1 state features.
    speed_obs: dict[tuple, list[tuple[float, float]]] = {}
    for e in events:
        try:
            if e["train_id"] and e["station"]:
                speed_obs.setdefault((e["train_id"], e["station"]), []).append(
                    (float(e["ts"]), float(e["speed_kmph"])))
        except (TypeError, ValueError):
            continue

    # Scheduled section entries (other-train congestion proxy, no leakage:
    # pure timetable info) keyed by (station j-1) -> sorted entry times.
    sched_entry: dict[str, list[float]] = {}
    by_train: dict[str, list[dict]] = {}
    for r in rows:
        by_train.setdefault(r["train_id"], []).append(r)
    for tid, rs in by_train.items():
        rs = sorted(rs, key=lambda r: int(r["seq"]))
        for r in rs:
            try:
                sched_entry.setdefault(r["station"], []).append(float(r["sched_dep"]))
            except (TypeError, ValueError):
                continue
    for v in sched_entry.values():
        v.sort()

    # Rake upstream map: rake -> earlier service final delay.
    order = sorted(by_train, key=lambda t: float(by_train[t][0]["sched_arr"]))
    first_of_rake: dict[str, str] = {}
    final_of_rake: dict[str, float] = {}
    for tid in order:
        rs = sorted(by_train[tid], key=lambda r: int(r["seq"]))
        rake = rs[0]["rake_id"]
        if rake not in first_of_rake:
            first_of_rake[rake] = tid
            final_of_rake[rake] = float(rs[-1]["delay_arr_min"])

    if not _s1._TRAINED or _s1._MODEL is None:
        _s1.train(verbose=False)

    trains = trains_from_rows(rows)
    sections = _sections_in_order(G)
    n_st = len(_stations_in_order(G))
    dataset: list[dict] = []
    for t in trains:
        actual = [float(r["delay_arr_min"]) for r in
                  sorted(by_train[t["train_id"]], key=lambda r: int(r["seq"]))]
        actual_dep = [float(r["actual_dep"]) for r in
                      sorted(by_train[t["train_id"]], key=lambda r: int(r["seq"]))]
        rake = t["rake_id"]
        upstream = (final_of_rake[rake] if first_of_rake.get(rake) != t["train_id"] else 0.0)
        for k in range(n_st - 1):
            hist = actual[: k + 1]
            d1 = hist[-1] - hist[-2] if len(hist) >= 2 else 0.0
            d2 = hist[-2] - hist[-3] if len(hist) >= 3 else 0.0
            d3 = hist[-3] - hist[-4] if len(hist) >= 4 else 0.0
            cands = speed_obs.get((t["train_id"], t["stops"][k]["station"]), [])
            speed = (min(cands, key=lambda c: abs(c[0] - actual_dep[k]))[1] if cands else 0.0)
            nxt_sec = f"{t['stops'][k]['station']}-{t['stops'][k + 1]['station']}"
            restr = 0.0
            for r in net["restrictions"]:
                if (r["section"] == nxt_sec
                        and r["start_min"] <= t["stops"][k]["sched_dep"] < r["end_min"]):
                    restr = 1.0
            s1_probs = _s1._scaled_proba_dict([d1, d2, d3, speed, restr, upstream])
            conf = max(s1_probs.values())
            tk = {**t, "cur_seq": k, "cur_delay": actual[k], "cur_ts": actual_dep[k]}
            preds = {p["seq"]: p for p in backbone(tk, actual_dep[k], G)}
            for j in range(k + 1, n_st):
                p = preds[j]
                entry = p["pred_dep"] if j - 1 == k else preds[j - 1]["pred_dep"]
                cong = sum(1 for e in sched_entry.get(t["stops"][j - 1]["station"], [])
                           if abs(e - entry) <= 15.0) - 1  # exclude self
                dataset.append(
                    {
                        "feat": [
                            p["delay"], p["drivers"]["run"], p["drivers"]["dwell"],
                            p["drivers"]["restriction"], p["drivers"]["recovery"],
                            max(0, cong), (p["pred_arr"] / 60.0) % 24.0,
                            SECTION_TYPE_ORD[sections[j - 1]["type"]],
                            s1_probs["grows"], s1_probs["stable"], s1_probs["recovers"],
                            float(j - k), actual[k],
                        ],
                        "target": actual[j] - p["delay"],
                        "horizon": _horizon_bucket(k, j, n_st),
                        "train_id": t["train_id"],
                        "baseline": actual[k],  # baseline-1 (schedule + current delay)
                    }
                )
    return dataset


def split_residual_rows(rows: list[dict]) -> tuple[list, list, list]:
    """Chronological 70/15/15 split by train departure order."""
    order = sorted({r["train_id"] for r in rows},
                   key=lambda tid: int(tid[1:]))  # T101..T140 depart in order
    n = len(order)
    fit_ids = set(order[: int(n * 0.70)])
    val_ids = set(order[int(n * 0.70): int(n * 0.85)])
    return ([r for r in rows if r["train_id"] in fit_ids],
            [r for r in rows if r["train_id"] in val_ids],
            [r for r in rows if r["train_id"] not in fit_ids | val_ids])


def fit_quantiles(rows_train: list[dict], random_state: int = 7) -> dict:
    """Fit one GradientBoostingRegressor per quantile alpha."""
    from sklearn.ensemble import GradientBoostingRegressor

    X = np.asarray([r["feat"] for r in rows_train])
    y = np.asarray([r["target"] for r in rows_train])
    models = {}
    for a in RESID_ALPHAS:
        # Depth-1 stumps: additive, robust to the sim's heavy-tailed noise.
        m = GradientBoostingRegressor(loss="quantile", alpha=a,
                                      n_estimators=200, max_depth=1,
                                      learning_rate=0.1, min_samples_leaf=30,
                                      subsample=0.8, random_state=random_state)
        m.fit(X, y)
        models[a] = m
    importances = dict(zip(RESID_FEATURES, models[0.5].feature_importances_))
    return {"models": models, "importances": importances}


def calibrate(models: dict, rows_cal: list[dict]) -> float:
    """Split-conformal: band widening so P10-P90 covers 80% on cal data."""
    X = np.asarray([r["feat"] for r in rows_cal])
    y = np.asarray([r["target"] for r in rows_cal])
    q10, q90 = models[0.1].predict(X), models[0.9].predict(X)
    scores = np.maximum(q10 - y, y - q90)
    n = len(scores)
    return float(np.quantile(scores, min(1.0, 0.8 * (1.0 + 1.0 / max(n, 1)))))


def evaluate(models: dict, adjustment: float, rows_test: list[dict]) -> dict:
    """Per-horizon MAE of median and P10-P90 coverage (model) + baseline MAE."""
    X = np.asarray([r["feat"] for r in rows_test])
    y = np.asarray([r["target"] for r in rows_test])
    q10 = models[0.1].predict(X) - adjustment
    q50 = models[0.5].predict(X)
    q90 = models[0.9].predict(X) + adjustment
    horizons = sorted({r["horizon"] for r in rows_test})
    out = {}
    for h in horizons:
        idx = np.array([i for i, r in enumerate(rows_test) if r["horizon"] == h])
        resid = y[idx] - q50[idx]
        mae = float(np.mean(np.abs(resid)))
        cov = float(np.mean((y[idx] >= q10[idx]) & (y[idx] <= q90[idx])))
        # Baseline-1 predicts delay_k flat: error vs actual = |actual - delay_k|
        # = |target + bb_delay - baseline| with bb_delay = feat[0].
        errs = [abs(rows_test[int(i)]["target"] + rows_test[int(i)]["feat"][0]
                    - rows_test[int(i)]["baseline"]) for i in idx]
        out[h] = {"mae_model": mae, "coverage": cov,
                  "mae_baseline": float(np.mean(errs)), "n": int(len(idx))}
    return out


def train_residual(verbose: bool = True) -> dict:
    """Fit quantile GBMs, conformal-calibrate, persist bundle, print metrics."""
    global _resid_bundle
    rows = build_residual_dataset()
    tr, cal, te = split_residual_rows(rows)
    fit = fit_quantiles(tr)
    adj = calibrate(fit["models"], cal)
    metrics = evaluate(fit["models"], adj, te)
    bundle = {"models": fit["models"], "adjustment": adj,
              "features": RESID_FEATURES, "importances": fit["importances"],
              "metrics": metrics,
              "n_train": len(tr), "n_cal": len(cal), "n_test": len(te)}
    RESID_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESID_PATH, "wb") as f:
        pickle.dump(bundle, f)
    _resid_bundle = bundle
    if verbose:
        print(f"[system2] residual GBM: n_train={len(tr)} n_cal={len(cal)} "
              f"n_test={len(te)} conformal_adj={adj:.3f}")
        top = sorted(fit["importances"].items(), key=lambda kv: -kv[1])[:5]
        print("[system2] top features: " + ", ".join(f"{k}={v:.3f}" for k, v in top))
        metrics_print(metrics)
    return {"adjustment": adj, "metrics": metrics,
            "n_train": len(tr), "n_cal": len(cal), "n_test": len(te)}


def metrics_print(m: dict | None = None) -> None:
    """Print per-horizon MAE (model + baseline-1) and model coverage."""
    if m is None:
        m = load_residual()["metrics"]
    print(f"{'horizon':<8} {'MAE model':>10} {'MAE base':>10} {'cover P10-P90':>14} {'n':>6}")
    for h in HORIZONS + ("mid",):
        if h in m:
            d = m[h]
            print(f"{h:<8} {d['mae_model']:>10.3f} {d['mae_baseline']:>10.3f} "
                  f"{d['coverage']:>14.3f} {d['n']:>6}")
    print("baseline-1 = schedule + current delay (point forecast: no interval, "
          "coverage n/a)")


def metrics() -> dict:
    """Train (if needed) and print MAE/coverage per horizon. Returns metrics."""
    load_residual()
    assert _resid_bundle is not None
    metrics_print(_resid_bundle["metrics"])
    return _resid_bundle["metrics"]


def load_residual() -> dict:
    """Lazy-load (or train) the residual bundle."""
    global _resid_bundle
    if _resid_bundle is not None:
        return _resid_bundle
    if RESID_PATH.exists():
        with open(RESID_PATH, "rb") as f:
            _resid_bundle = pickle.load(f)
        return _resid_bundle
    train_residual(verbose=False)
    assert _resid_bundle is not None
    return _resid_bundle


def residual_correct(bb_delay: float, feat: list[float], confidence: float) -> dict:
    """Backbone delay + residual quantiles -> p10/p50/p90, learned, p_on_time."""
    b = load_residual()
    X = np.asarray([feat])
    q10 = float(b["models"][0.1].predict(X)[0]) - b["adjustment"]
    q50 = float(b["models"][0.5].predict(X)[0])
    q90 = float(b["models"][0.9].predict(X)[0]) + b["adjustment"]
    widen = 1.0 + (1.0 - confidence)
    p50 = bb_delay + q50
    p10 = p50 - (q50 - q10) * widen
    p90 = p50 + (q90 - q50) * widen
    p_on_time = float(np.interp(ONTIME_THRESHOLD_MIN, [p10, p50, p90],
                                [0.1, 0.5, 0.9], left=0.0, right=1.0))
    return {"p10": round(p10, 1), "p50": round(p50, 1), "p90": round(p90, 1),
            "p_on_time": round(p_on_time, 3), "learned": round(q50, 1)}


def predict_full(train: dict, now_min: float, G: nx.DiGraph,
                 regime_probs: dict, confidence: float) -> list[dict]:
    """Backbone + residual quantiles per station (the 'full' path, T08/T09).

    ``regime_probs`` = {"grows","stable","recovers"} from System 1 for this
    train; confidence widens the bands. Returns per-station dicts with
    p10/p50/p90 arrival-delay minutes, p_on_time, drivers (learned filled),
    and flags.
    """
    data_dir = Path(__file__).resolve().parent.parent / "data"
    # Schedule-based congestion proxy (timetable info only, no leakage).
    sched_entry: dict[str, list[float]] = {}
    for row in csv.DictReader(open(data_dir / "trains.csv")):
        try:
            sched_entry.setdefault(row["station"], []).append(float(row["sched_dep"]))
        except (TypeError, ValueError):
            continue
    sections = _sections_in_order(G)
    bb = backbone(train, now_min, G)
    pred_dep = {p["seq"]: p["pred_dep"] for p in bb}
    out = []
    for p in bb:
        i = p["seq"]
        if i <= train.get("cur_seq", -1):
            d = p["delay"]
            out.append({**p, "delay_p10": d, "delay_p50": d, "delay_p90": d,
                        "p_on_time": 1.0 if d <= ONTIME_THRESHOLD_MIN else 0.0})
            continue
        entry = pred_dep[i - 1]
        cong = max(0, sum(1 for e in sched_entry.get(train["stops"][i - 1]["station"], [])
                          if abs(e - entry) <= 15.0) - 1)
        feat = [
            p["delay"], p["drivers"]["run"], p["drivers"]["dwell"],
            p["drivers"]["restriction"], p["drivers"]["recovery"],
            cong, (p["pred_arr"] / 60.0) % 24.0,
            SECTION_TYPE_ORD[sections[i - 1]["type"]],
            regime_probs.get("grows", 1 / 3), regime_probs.get("stable", 1 / 3),
            regime_probs.get("recovers", 1 / 3),
            float(i - train.get("cur_seq", -1)), train.get("cur_delay", 0.0),
        ]
        corr = residual_correct(p["delay"], feat, confidence)
        drivers = dict(p["drivers"])
        drivers["learned"] = corr["learned"]
        out.append({**p, "delay_p10": corr["p10"], "delay_p50": corr["p50"],
                    "delay_p90": corr["p90"], "p_on_time": corr["p_on_time"],
                    "drivers": drivers})
    return out


def delay_matrix(now_min: float, trains: list[dict], G: nx.DiGraph) -> dict:
    """Stations x trains table of predicted arrival-delay minutes.

    Returns {"stations": [...], "trains": [...], "values": [[delay...]]}
    with values[row=station][col=train]; never NaN (missing -> 0.0).
    """
    stations = _stations_in_order(G)
    values = []
    for si in range(len(stations)):
        row = []
        for t in trains:
            preds = {p["seq"]: p for p in backbone(t, now_min, G)}
            p = preds.get(si)
            row.append(round(float(p["delay"]), 1) if p is not None else 0.0)
        values.append(row)
    return {
        "stations": stations,
        "trains": [t["train_id"] for t in trains],
        "values": values,
    }


def whatif(
    train_id: str,
    hold_min: float,
    station: str,
    trains: list[dict],
    G: nx.DiGraph,
    now_min: float,
) -> dict:
    """Re-run backbone with a hold; diff vs baseline, labelled scenario."""
    train = next(t for t in trains if t["train_id"] == train_id)
    base = {p["seq"]: p for p in backbone(train, now_min, G)}
    scen = {p["seq"]: p for p in backbone(
        train, now_min, G, hold={"station": station, "minutes": hold_min})}
    hold_seq = next(i for i, s in enumerate(train["stops"]) if s["station"] == station)
    diffs = [
        {
            "station": train["stops"][i]["station"],
            "baseline_arr": base[i]["pred_arr"],
            "scenario_arr": scen[i]["pred_arr"],
            "delta_min": round(scen[i]["pred_arr"] - base[i]["pred_arr"], 1),
        }
        for i in range(hold_seq, len(train["stops"]))
    ]
    return {
        "label": "scenario",
        "train_id": train_id,
        "station": station,
        "hold_min": hold_min,
        "diffs": diffs,
    }
