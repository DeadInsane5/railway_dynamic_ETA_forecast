"""T03 — Ontology + validation (ALL DATA SIMULATED).

In-memory networkx DiGraph of the simulated rail corridor plus a pure
observation validator. All stations, sections, trains, rakes, restrictions
and events are synthetic demo data (see ADR-0016); nothing here reflects
real operations.
"""

from __future__ import annotations

import networkx as nx

STATION_INDEX: dict[str, int] = {f"S{i}": i - 1 for i in range(1, 13)}

_REQUIRED_FIELDS = ("train_id", "ts", "station", "delay_min", "state")


def _fnum(v) -> float | None:
    try:
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def build(network: dict, trains: list[dict]) -> nx.DiGraph:
    """Build the ontology graph from parsed network.json + trains.csv rows."""
    G = nx.DiGraph()

    stations: list[dict] = network.get("stations", [])
    sections: list[dict] = network.get("sections", [])
    restrictions: list[dict] = network.get("restrictions", [])

    station_ids = [s["id"] for s in stations]
    station_index = {sid: i for i, sid in enumerate(station_ids)}

    for s in stations:
        G.add_node(s["id"], kind="Station", name=s.get("name", ""))

    for i in range(len(station_ids) - 1):
        G.add_edge(station_ids[i], station_ids[i + 1], rel="NEXT")

    for sec in sections:
        G.add_node(
            sec["id"],
            kind="Section",
            **{
                "from": sec.get("from"),
                "to": sec.get("to"),
                "km": sec.get("km"),
                "sched_run_min": sec.get("sched_run_min"),
                "type": sec.get("type"),
            },
        )

    # Group train rows by train_id.
    by_train: dict[str, dict] = {}
    for row in trains:
        tid = str(row.get("train_id", ""))
        if not tid:
            continue
        entry = by_train.setdefault(
            tid, {"name": row.get("name", ""), "rake_id": row.get("rake_id", ""), "deps": []}
        )
        if row.get("name"):
            entry["name"] = row["name"]
        if row.get("rake_id"):
            entry["rake_id"] = row["rake_id"]
        dep = _fnum(row.get("sched_dep"))
        if dep is not None:
            entry["deps"].append(dep)

    for tid, entry in by_train.items():
        first_dep = min(entry["deps"]) if entry["deps"] else float("inf")
        G.add_node(
            tid,
            kind="Train",
            name=entry["name"],
            rake_id=entry["rake_id"],
            first_sched_dep=first_dep,
            last_seq=None,
            last_ts=None,
            last_delay=0.0,
        )

    rake_ids = sorted({e["rake_id"] for e in by_train.values() if e["rake_id"]})
    for rid in rake_ids:
        G.add_node(rid, kind="Rake")

    section_ids = [sec["id"] for sec in sections]
    for tid, entry in by_train.items():
        rid = entry["rake_id"]
        if rid:
            G.add_edge(tid, rid, rel="FORMS", key="FORMS")
        for sec_id in section_ids:
            G.add_edge(tid, sec_id, rel="RUNS_ON")

    # FEEDS_RAKE: rake -> later service(s), ordered by first sched_dep.
    rake_to_trains: dict[str, list[str]] = {}
    for tid, entry in by_train.items():
        if entry["rake_id"]:
            rake_to_trains.setdefault(entry["rake_id"], []).append(tid)

    def _dep(tid: str) -> float:
        v = G.nodes[tid].get("first_sched_dep", float("inf"))
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("inf")

    for rid, tids in rake_to_trains.items():
        if len(tids) < 2:
            continue
        ordered = sorted(tids, key=_dep)
        for later in ordered[1:]:
            G.add_edge(rid, later, rel="FEEDS_RAKE", key="FEEDS_RAKE")

    for r in restrictions:
        G.add_node(
            r["id"],
            kind="Restriction",
            section=r.get("section"),
            start_min=r.get("start_min"),
            end_min=r.get("end_min"),
            extra_min=r.get("extra_min"),
            cause=r.get("cause"),
        )
        if r.get("section") in G:
            G.add_edge(r["id"], r["section"], rel="AFFECTS")

    G.graph["station_index"] = station_index
    G.graph["restrictions"] = [dict(r) for r in restrictions]
    G.graph["event_counter"] = 0
    return G


def ingest(G: nx.DiGraph, obs: dict) -> None:
    """Add an Event node for obs and update the Train node's live state."""
    counter = int(G.graph.get("event_counter", 0))
    eid = f"E{counter}"
    G.graph["event_counter"] = counter + 1

    G.add_node(eid, kind="Event", **{k: obs.get(k) for k in obs})

    tid = obs.get("train_id")
    if tid is None:
        return
    tid = str(tid)
    if tid not in G:
        G.add_node(
            tid,
            kind="Train",
            name="",
            rake_id="",
            first_sched_dep=float("inf"),
            last_seq=None,
            last_ts=None,
            last_delay=0.0,
        )
    station_index = G.graph.get("station_index", STATION_INDEX)
    station = obs.get("station")
    seq = station_index.get(station, -1) if station is not None else -1
    ts = _fnum(obs.get("ts"))
    delay = _fnum(obs.get("delay_min"))

    G.nodes[tid]["last_seq"] = seq
    G.nodes[tid]["last_ts"] = ts
    if delay is not None:
        G.nodes[tid]["last_delay"] = delay
    G.add_edge(tid, eid, rel="EVENT")


def upstream_delay(G: nx.DiGraph, train_id: str) -> float:
    """Return the earlier same-rake sibling's last known delay, else 0.0."""
    if train_id not in G:
        return 0.0
    node = G.nodes[train_id]
    rake_id = node.get("rake_id")
    if not rake_id:
        return 0.0
    try:
        my_dep = float(node.get("first_sched_dep", float("inf")))
    except (TypeError, ValueError):
        my_dep = float("inf")
    best_dep: float | None = None
    best_delay = 0.0
    for n, attrs in G.nodes(data=True):
        if attrs.get("kind") != "Train" or n == train_id:
            continue
        if attrs.get("rake_id") != rake_id:
            continue
        try:
            dep = float(attrs.get("first_sched_dep", float("inf")))
        except (TypeError, ValueError):
            continue
        if dep < my_dep and (best_dep is None or dep > best_dep):
            best_dep = dep
            try:
                best_delay = float(attrs.get("last_delay", 0.0) or 0.0)
            except (TypeError, ValueError):
                best_delay = 0.0
    return best_delay


def trains_in_section(
    G: nx.DiGraph, section_id: str, now_min: float, window_min: float = 30.0
) -> int:
    """Count distinct trains with Event nodes in section within [now-window, now]."""
    lo = now_min - window_min
    seen: set[str] = set()
    for _n, attrs in G.nodes(data=True):
        if attrs.get("kind") != "Event":
            continue
        if attrs.get("section") != section_id:
            continue
        ts = _fnum(attrs.get("ts"))
        if ts is None:
            continue
        if lo <= ts <= now_min:
            tid = attrs.get("train_id")
            if tid is not None:
                seen.add(str(tid))
    return len(seen)


def restrictions_on(G: nx.DiGraph, section_id: str, now_min: float) -> list[dict]:
    """Return active restriction dicts for the section at now_min."""
    restrictions = G.graph.get("restrictions")
    if restrictions is None:
        restrictions = [
            {
                "id": n,
                "section": a.get("section"),
                "start_min": a.get("start_min"),
                "end_min": a.get("end_min"),
                "extra_min": a.get("extra_min"),
                "cause": a.get("cause"),
            }
            for n, a in G.nodes(data=True)
            if a.get("kind") == "Restriction"
        ]
    out: list[dict] = []
    for r in restrictions:
        if r.get("section") != section_id:
            continue
        try:
            start = float(r["start_min"])
            end = float(r["end_min"])
        except (TypeError, ValueError, KeyError):
            continue
        if start <= now_min < end:
            out.append(dict(r))
    return out


def validate(obs: dict, train_state: dict | None) -> tuple[bool, str]:
    """Pure observation check: missing fields, ranges, station order."""
    for name in _REQUIRED_FIELDS:
        if name not in obs:
            return False, f"missing field: {name}"
        v = obs[name]
        if v is None:
            return False, f"missing field: {name}"
        if isinstance(v, str) and v.strip() == "":
            return False, f"missing field: {name}"

    ts = _fnum(obs.get("ts"))
    if ts is None:
        return False, f"unparseable ts: {obs.get('ts')!r}"
    delay = _fnum(obs.get("delay_min"))
    if delay is None:
        return False, f"unparseable delay_min: {obs.get('delay_min')!r}"

    if delay < -30 or delay > 600:
        return False, f"delay out of range: {delay}"

    station = obs.get("station")
    if station not in STATION_INDEX:
        return False, f"unknown station: {station}"

    if train_state is not None and train_state.get("last_seq") is not None:
        try:
            last_seq = int(train_state["last_seq"])
        except (TypeError, ValueError):
            last_seq = None
        if last_seq is not None and STATION_INDEX[station] < last_seq:
            return (
                False,
                f"station order goes backwards: {station} "
                f"(index {STATION_INDEX[station]}) after seq {last_seq}",
            )

    return True, "ok"
