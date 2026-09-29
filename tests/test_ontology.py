"""T03 tests — ontology + validation (simulated data only)."""

import csv
import json
from pathlib import Path

import networkx as nx

from app import ontology
from app.ontology import (
    STATION_INDEX,
    build,
    ingest,
    restrictions_on,
    trains_in_section,
    upstream_delay,
    validate,
)

DATA = Path(__file__).resolve().parent.parent / "data"


def _load():
    network = json.loads((DATA / "network.json").read_text())
    with open(DATA / "trains.csv") as f:
        trains = list(csv.DictReader(f))
    return network, trains


def _obs(**kw):
    base = {
        "train_id": "T101",
        "ts": 500.0,
        "station": "S5",
        "section": "S4-S5",
        "delay_min": 5.0,
        "speed_kmph": 60.0,
        "state": "running",
        "source": "sim",
        "remark": "",
    }
    base.update(kw)
    return base


def test_build_node_kinds_and_feeds_rake():
    network, trains = _load()
    G = build(network, trains)
    kinds = {a.get("kind") for _, a in G.nodes(data=True)}
    assert {"Station", "Section", "Train", "Rake", "Restriction"} <= kinds

    n_station = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Station")
    n_section = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Section")
    n_train = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Train")
    n_rake = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Rake")
    n_restr = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Restriction")
    assert n_station == 12
    assert n_section == 11
    assert n_train == 40
    assert n_rake == 32
    assert n_restr == len(network["restrictions"])

    feeds = [(u, v) for u, v, a in G.edges(data=True) if a.get("rel") == "FEEDS_RAKE"]
    # R01..R08 serve two services -> 8 rake->second-service edges.
    assert len(feeds) == 8
    for u, v in feeds:
        assert G.nodes[u].get("kind") == "Rake"
        assert G.nodes[v].get("kind") == "Train"

    forms = [(u, v) for u, v, a in G.edges(data=True) if a.get("rel") == "FORMS"]
    assert len(forms) == 40
    nxt = [(u, v) for u, v, a in G.edges(data=True) if a.get("rel") == "NEXT"]
    assert len(nxt) == 11
    assert G.graph["station_index"] == {f"S{i}": i - 1 for i in range(1, 13)}
    assert STATION_INDEX == {f"S{i}": i - 1 for i in range(1, 13)}


def test_validate_backwards_station_fails():
    ok, reason = validate(_obs(station="S5"), {"last_seq": 4, "last_ts": 490.0})
    assert ok is True
    ok, reason = validate(_obs(station="S2"), {"last_seq": 4, "last_ts": 1400.0})
    assert ok is False
    assert isinstance(reason, str) and len(reason) > 0
    assert "backwards" in reason


def test_validate_delay_out_of_range():
    ok, reason = validate(_obs(delay_min=-45.0), None)
    assert ok is False
    assert "delay out of range" in reason


def test_validate_missing_field():
    bad = _obs()
    del bad["state"]
    ok, reason = validate(bad, None)
    assert ok is False
    assert "missing field" in reason
    ok, reason = validate(_obs(ts=""), None)
    assert ok is False
    assert "missing field" in reason


def test_ingest_updates_train_state():
    network, trains = _load()
    G = build(network, trains)
    assert G.nodes["T101"]["last_seq"] is None
    ingest(G, _obs(train_id="T101", ts=500.0, station="S5", delay_min=7.5))
    assert G.nodes["T101"]["last_seq"] == 4
    assert G.nodes["T101"]["last_ts"] == 500.0
    assert G.nodes["T101"]["last_delay"] == 7.5
    events = [n for n, a in G.nodes(data=True) if a.get("kind") == "Event"]
    assert len(events) == 1
    assert G.has_edge("T101", events[0])


def test_upstream_delay_inherits_from_earlier_sibling():
    network, trains = _load()
    G = build(network, trains)
    feeds = [(u, v) for u, v, a in G.edges(data=True) if a.get("rel") == "FEEDS_RAKE"]
    assert feeds
    rake, later = feeds[0]
    earlier = [
        n
        for n, a in G.nodes(data=True)
        if a.get("kind") == "Train"
        and a.get("rake_id") == rake
        and n != later
    ][0]
    assert upstream_delay(G, later) == 0.0
    ingest(
        G,
        _obs(
            train_id=earlier,
            ts=400.0,
            station="S12",
            section="S11-S12",
            delay_min=25.0,
        ),
    )
    assert upstream_delay(G, later) == 25.0
    assert upstream_delay(G, earlier) == 0.0


def test_restrictions_on_inside_and_outside():
    network, trains = _load()
    G = build(network, trains)
    r = network["restrictions"][0]
    inside = restrictions_on(G, r["section"], float(r["start_min"]))
    assert any(d["id"] == r["id"] for d in inside)
    outside = restrictions_on(G, r["section"], float(r["end_min"]) + 1000.0)
    assert all(d["id"] != r["id"] for d in outside)
    assert restrictions_on(G, "NOPE", 500.0) == []


def test_trains_in_section_tiny_graph():
    tiny_net = {
        "stations": [{"id": "S1", "name": "A"}, {"id": "S2", "name": "B"}],
        "sections": [
            {"id": "S1-S2", "from": "S1", "to": "S2", "km": 10,
             "sched_run_min": 8.0, "type": "plain"}
        ],
        "restrictions": [],
    }
    tiny_trains = [
        {"train_id": "T1", "name": "A", "rake_id": "R1", "sched_dep": "100"},
        {"train_id": "T2", "name": "B", "rake_id": "R2", "sched_dep": "110"},
    ]
    G = build(tiny_net, tiny_trains)
    ingest(G, _obs(train_id="T1", ts=200.0, station="S2",
                   section="S1-S2", delay_min=1.0))
    ingest(G, _obs(train_id="T1", ts=205.0, station="S2",
                   section="S1-S2", delay_min=2.0))
    ingest(G, _obs(train_id="T2", ts=100.0, station="S2",
                   section="S1-S2", delay_min=0.0))
    assert trains_in_section(G, "S1-S2", 210.0, window_min=30.0) == 1
    assert trains_in_section(G, "S1-S2", 210.0, window_min=120.0) == 2
    assert trains_in_section(G, "OTHER", 210.0) == 0
    assert isinstance(G, nx.DiGraph)
