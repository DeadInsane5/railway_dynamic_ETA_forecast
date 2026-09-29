"""T01 — Simulator + demo dataset (ALL DATA SIMULATED).

Generates, with a fixed seed:
- ``data/network.json``: 12 stations, 11 sections (per INDEX contract).
- ``data/trains.csv``: ground truth, one row per train per station
  (schedule + actuals). Used for training/eval — adapters never read this.
- ``data/events.csv``: observation stream replayed by adapters (T02),
  one row per train per station plus ~2% bad records and 3 free-text remarks.

Delay process per train per station::
    delay[i+1] = delay[i] + noise + restriction_effect + congestion_effect - recovery

Run: ``python -m app.sim`` (from the project root).
"""

from __future__ import annotations

import csv
import json
import os
import random
from pathlib import Path

SEED = 42
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

N_STATIONS = 12
N_TRAINS = 40
HEADWAY_MIN = 6.0        # congestion: min separation entering a section
DWELL_MIN = 2.0          # scheduled halt at each station
JUNCTION_DWELL_MIN = 3.0
MIN_TURNAROUND_MIN = 30.0  # rake turnaround before inherited delay bites

STATION_NAMES = [
    "NDLS", "GZB", "ALJN", "TDL", "CNB", "LKO", "AY",
    "BSB", "MGS", "PNBE", "KIUL", "HWH",
]

# (km, type) per section S_i -> S_{i+1}
SECTION_SPECS = [
    (18, "plain"), (22, "plain"), (15, "loop"), (28, "plain"),
    (20, "junction"), (25, "plain"), (12, "loop"), (30, "plain"),
    (24, "plain"), (16, "junction"), (35, "plain"),
]

SECTION_SPEED = {"plain": 75.0, "loop": 55.0, "junction": 50.0}  # km/h normal

FREE_TEXT_REMARKS = [
    "signal failure near S5, trains being piloted",
    "dense fog near S8, visibility below 50m",
    "track maintenance at S3, single-line working",
]


def build_network(rng: random.Random) -> dict:
    stations = [f"S{i + 1}" for i in range(N_STATIONS)]
    sections = []
    for i, (km, stype) in enumerate(SECTION_SPECS):
        v = SECTION_SPEED[stype]
        sched_run_min = round(km / v * 60.0, 1)
        sections.append(
            {
                "id": f"{stations[i]}-{stations[i + 1]}",
                "from": stations[i],
                "to": stations[i + 1],
                "km": km,
                "sched_run_min": sched_run_min,
                "type": stype,
            }
        )
    # 2-3 random speed restrictions: extra minutes if traversed inside window.
    restrictions = []
    idxs = rng.sample(range(len(sections)), 3)
    for k, s_idx in enumerate(idxs):
        sec = sections[s_idx]
        start = rng.choice([360, 540, 720, 960])  # 06:00, 09:00, 12:00, 16:00
        restrictions.append(
            {
                "id": f"R{k + 1}",
                "section": sec["id"],
                "start_min": start,
                "end_min": start + rng.choice([90, 120, 150]),
                "extra_min": round(rng.uniform(4.0, 10.0), 1),
                "cause": rng.choice(["track work", "signal work", "bridge inspection"]),
            }
        )
    return {
        "stations": [{"id": s, "name": n} for s, n in zip(stations, STATION_NAMES)],
        "sections": sections,
        "restrictions": restrictions,
    }


def restriction_extra(network: dict, section_id: str, now_min: float) -> float:
    extra = 0.0
    for r in network["restrictions"]:
        if r["section"] == section_id and r["start_min"] <= now_min < r["end_min"]:
            extra += r["extra_min"]
    return extra


def simulate(rng: random.Random, network: dict):
    """Simulate 40 trains. Returns (train_rows, event_rows).

    train_rows: ground-truth per train per station.
    event_rows: observation stream (valid rows; bad rows added later).
    """
    stations = [s["id"] for s in network["stations"]]
    sections = network["sections"]

    # Departures from S1 spread across the day: 05:00 -> 20:00.
    dep0 = [300.0 + i * (900.0 / N_TRAINS) + rng.uniform(-4, 4) for i in range(N_TRAINS)]
    dep0.sort()
    train_ids = [f"T{101 + i}" for i in range(N_TRAINS)]

    # Rakes: 32 rakes; R01..R08 serve a second service (last 8 trains reuse them),
    # so a late first service passes delay to the second (ADR-0021 style).
    rake_of = {}
    for i in range(N_TRAINS):
        rake_of[train_ids[i]] = f"R{(i % 32) + 1:02d}" if i >= N_TRAINS - 8 else f"R{i + 1:02d}"
    # Map rake -> first-service train (for inheritance lookup).
    rake_first = {}
    for tid in train_ids[: N_TRAINS - 8]:
        rake_first.setdefault(rake_of[tid], tid)

    first_arr_delay: dict[str, float] = {}  # train_id -> arrival delay at S12
    first_arr_time: dict[str, float] = {}   # train_id -> actual arrival at S12
    section_last_entry: dict[str, float] = {}  # congestion state

    train_rows: list[dict] = []
    event_rows: list[dict] = []

    for order, tid in enumerate(train_ids):
        sched_dep = dep0[order]
        # Rake inheritance: second service starts late if the first arrived
        # late beyond the minimum turnaround.
        delay = rng.uniform(0, 4)  # small initial perturbation
        rake = rake_of[tid]
        if order >= N_TRAINS - 8 and rake in rake_first:
            # Second service on a reused rake: inherit lateness beyond turnaround.
            first = rake_first[rake]
            turnaround = sched_dep - first_arr_time[first]
            inherited = max(0.0, first_arr_delay[first] - max(0.0, turnaround - MIN_TURNAROUND_MIN))
            delay = max(delay, inherited)

        t_sched = sched_dep  # scheduled time at current station (departure basis)
        t_actual = sched_dep + delay
        for i, st in enumerate(stations):
            dwell = JUNCTION_DWELL_MIN if (i > 0 and sections[i - 1]["type"] == "junction") else DWELL_MIN
            if i == 0:
                sched_arr, sched_dep_st = t_sched, t_sched
                actual_arr, actual_dep = t_actual, t_actual
            else:
                sec = sections[i - 1]
                sched_arr = t_sched + sec["sched_run_min"]
                sched_dep_st = sched_arr + dwell
                # --- delay process ---
                noise = rng.gauss(0, 1.2) + rng.choice([-1, 1]) * rng.expovariate(0.8)
                restriction = restriction_extra(network, sec["id"], t_actual)
                gap = t_actual - section_last_entry.get(sec["id"], -1e9)
                congestion = max(0.0, HEADWAY_MIN - gap) if gap < HEADWAY_MIN else 0.0
                slack = 1.5  # scheduled recovery margin per section
                recovery = min(slack, 0.5 * max(0.0, delay))
                delay = delay + noise + restriction + congestion - recovery
                actual_arr = sched_arr + delay
                # Departure cannot precede schedule by more than the dwell buffer;
                # holding for congestion also applies at departure.
                actual_dep = max(actual_arr + 0.5, sched_dep_st + delay)
                delay = actual_dep - sched_dep_st  # carry departure delay forward
                t_sched = sched_dep_st
                t_actual = actual_dep
                section_last_entry[sec["id"]] = actual_dep - sec["sched_run_min"]

            delay_r = round(delay, 1)
            train_rows.append(
                {
                    "train_id": tid,
                    "name": f"Sim Express {tid[1:]}",
                    "rake_id": rake,
                    "station": st,
                    "seq": i,
                    "sched_arr": round(sched_arr, 1),
                    "sched_dep": round(sched_dep_st, 1),
                    "actual_arr": round(actual_arr, 1),
                    "actual_dep": round(actual_dep, 1),
                    "delay_arr_min": delay_r,
                }
            )
            # Observation row (valid; ts = actual arrival, minutes-of-day).
            speed = SECTION_SPEED[sections[i - 1]["type"]] if i > 0 else 0.0
            speed = max(0.0, rng.gauss(speed, 6)) if i > 0 else 0.0
            state = "at_station"
            if delay_r - (train_rows[-2]["delay_arr_min"] if len(train_rows) > 1 and train_rows[-2]["train_id"] == tid else 0) > 8:
                state = "halted"
            event_rows.append(
                {
                    "train_id": tid,
                    "ts": round(actual_arr, 1),
                    "station": st,
                    "section": sections[i - 1]["id"] if i > 0 else "",
                    "delay_min": delay_r,
                    "speed_kmph": round(speed, 1),
                    "state": state,
                    "remark": "",
                }
            )
            t_sched, t_actual = sched_dep_st, actual_dep

        first_arr_delay[tid] = train_rows[-1]["delay_arr_min"]
        first_arr_time[tid] = train_rows[-1]["actual_arr"]

    return train_rows, event_rows


def inject_bad_rows(rng: random.Random, event_rows: list[dict]) -> list[dict]:
    """Add ~2% bad records + 3 free-text remarks (valid rows)."""
    rows = [dict(r) for r in event_rows]
    # Free-text remarks on 3 random valid rows.
    for remark, row in zip(FREE_TEXT_REMARKS, rng.sample(rows, 3)):
        row["remark"] = remark
    n_bad = max(1, round(len(rows) * 0.02))
    trains = sorted({r["train_id"] for r in rows})
    bad: list[dict] = []
    for b in range(n_bad):
        kind = b % 3
        if kind == 0:  # out-of-range delay
            bad.append(
                {
                    "train_id": rng.choice(trains), "ts": round(rng.uniform(300, 1300), 1),
                    "station": "S5", "section": "S4-S5", "delay_min": -45.0,
                    "speed_kmph": 60.0, "state": "running", "remark": "",
                }
            )
        elif kind == 1:  # backwards station order (valid station, inconsistent ts)
            bad.append(
                {
                    "train_id": trains[0], "ts": 1400.0 + b,
                    "station": "S2", "section": "S1-S2", "delay_min": 5.0,
                    "speed_kmph": 70.0, "state": "running", "remark": "",
                }
            )
        else:  # missing fields
            bad.append(
                {
                    "train_id": rng.choice(trains), "ts": "",
                    "station": "", "section": "", "delay_min": "",
                    "speed_kmph": "", "state": "", "remark": "",
                }
            )
    rows.extend(bad)
    # Sort numerically by ts where possible; blank-ts rows go last.
    def _key(r):
        try:
            return float(r["ts"])
        except (TypeError, ValueError):
            return 1e9
    rows.sort(key=_key)
    return rows


def write_outputs(network: dict, train_rows: list[dict], event_rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "network.json").write_text(json.dumps(network, indent=2))
    with open(DATA_DIR / "trains.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(train_rows[0].keys()))
        w.writeheader()
        w.writerows(train_rows)
    with open(DATA_DIR / "events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(event_rows[0].keys()))
        w.writeheader()
        w.writerows(event_rows)


def main() -> None:
    rng = random.Random(SEED)
    network = build_network(rng)
    train_rows, event_rows = simulate(rng, network)
    event_rows = inject_bad_rows(rng, event_rows)
    write_outputs(network, train_rows, event_rows)

    import statistics

    delays = [r["delay_arr_min"] for r in train_rows]
    delays_sorted = sorted(delays)
    p95 = delays_sorted[max(0, int(0.95 * len(delays_sorted)) - 1)]
    print(f"seed={SEED} trains={N_TRAINS} stations={N_STATIONS}")
    print(f"network: {len(network['sections'])} sections, "
          f"{len(network['restrictions'])} restrictions")
    print(f"trains.csv rows={len(train_rows)} events.csv rows={len(event_rows)}")
    print(f"mean delay={statistics.mean(delays):.2f} min "
          f"p95 delay={p95:.2f} min "
          f"max delay={max(delays):.2f} min")
    print(f"bad-record demo rows included (~2%); remarks: {len(FREE_TEXT_REMARKS)}")
    print("wrote data/network.json data/trains.csv data/events.csv")


if __name__ == "__main__":
    # Allow `python -m app.sim` from project root.
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
