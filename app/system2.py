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


def _congestion_wait(G: nx.DiGraph, section_id: str, entry_ts: float) -> float:
    """Headway wait from the latest observed entry into this section."""
    latest = None
    for _, d in G.nodes(data=True):
        if d.get("kind") == "Event" and d.get("section") == section_id:
            try:
                ts = float(d.get("ts"))
            except (TypeError, ValueError):
                continue
            if ts <= entry_ts and (latest is None or ts > latest):
                latest = ts
    if latest is None:
        return 0.0
    return max(0.0, HEADWAY_MIN - (entry_ts - latest))


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
        wait = _congestion_wait(G, sec["id"], dep_time)
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

    _ = now_min  # reserved for future staleness handling (T09 owns fallback)
    return out


def baseline(train: dict) -> list[dict]:
    """Baseline 1 (T07 comparison): schedule + current delay, flat.

    Returns per-station {"station", "seq", "delay"} with delay = cur_delay.
    """
    d = round(train.get("cur_delay", 0.0), 1)
    return [{"station": s["station"], "seq": i, "delay": d}
            for i, s in enumerate(train["stops"])]


# ---------------------------------------------------------------- matrix / what-if

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
