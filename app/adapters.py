"""Mock data adapters (NTES / COA / RTIS) — ALL DATA SIMULATED (prototype only).

Each adapter replays rows from ``data/events.csv`` up to ``now_min`` minus
its feed lag. Unparseable rows (blank train_id/ts/station/delay_min or
non-numeric ts/delay_min) are skipped here; validation of bad *values*
(out-of-range delays, inconsistent station/ts, ...) is T03/T04's job.
"""

from __future__ import annotations

import csv
from pathlib import Path

EVENTS_CSV = Path(__file__).resolve().parent.parent / "data" / "events.csv"

OBSERVATION_KEYS = frozenset(
    {
        "train_id",
        "ts",
        "station",
        "section",
        "delay_min",
        "speed_kmph",
        "state",
        "source",
        "remark",
    }
)

VALID_STATES = frozenset({"running", "halted", "at_station"})


def _parse_float(raw) -> float | None:
    try:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        return float(text)
    except (ValueError, TypeError):
        return None


def _load_rows(csv_path: Path | str | None = None) -> list[dict]:
    """Read events CSV, skipping only unparseable rows.

    Skips rows with missing/blank train_id, ts, station, or delay_min
    (ts/delay_min must also parse as float). Every other row — including
    rows with out-of-range or inconsistent *values* — is kept for T03/T04.
    """
    path = Path(csv_path) if csv_path is not None else EVENTS_CSV
    rows: list[dict] = []
    with open(path, newline="") as f:
        for record in csv.DictReader(f):
            train_id = (record.get("train_id") or "").strip()
            station = (record.get("station") or "").strip()
            ts = _parse_float(record.get("ts"))
            delay_min = _parse_float(record.get("delay_min"))
            if not train_id or not station or ts is None or delay_min is None:
                continue
            state = (record.get("state") or "").strip()
            if state not in VALID_STATES:
                state = "running"
            rows.append(
                {
                    "train_id": train_id,
                    "ts": ts,
                    "station": station,
                    "section": (record.get("section") or "").strip(),
                    "delay_min": delay_min,
                    "speed_kmph": _parse_float(record.get("speed_kmph")),
                    "state": state,
                    "remark": (record.get("remark") or "").strip(),
                }
            )
    return rows


class _BaseAdapter:
    lag_min: float = 0.0
    source: str = "BASE"

    def __init__(self, csv_path: Path | str | None = None) -> None:
        self.csv_path = csv_path

    def poll(self, now_min: float) -> list[dict]:
        raise NotImplementedError


class NTESAdapter(_BaseAdapter):
    """Running status only (train, station, delay); 5-min lag."""

    lag_min = 5.0
    source = "NTES"

    def poll(self, now_min: float) -> list[dict]:
        cutoff = now_min - self.lag_min
        observations = []
        for row in _load_rows(self.csv_path):
            if row["ts"] <= cutoff:
                observations.append(
                    {
                        "train_id": row["train_id"],
                        "ts": row["ts"],
                        "station": row["station"],
                        "section": row["section"],
                        "delay_min": row["delay_min"],
                        "speed_kmph": None,
                        "state": row["state"],
                        "source": self.source,
                        "remark": "",
                    }
                )
        return observations


class COAAdapter(_BaseAdapter):
    """Adds remark free text and restriction notices; 5-min lag."""

    lag_min = 5.0
    source = "COA"

    def poll(self, now_min: float) -> list[dict]:
        cutoff = now_min - self.lag_min
        observations = []
        for row in _load_rows(self.csv_path):
            if row["ts"] <= cutoff:
                observations.append(
                    {
                        "train_id": row["train_id"],
                        "ts": row["ts"],
                        "station": row["station"],
                        "section": row["section"],
                        "delay_min": row["delay_min"],
                        "speed_kmph": None,
                        "state": row["state"],
                        "source": self.source,
                        "remark": row["remark"],
                    }
                )
        return observations


class RTISAdapter(_BaseAdapter):
    """Adds speed_kmph; 1-min lag."""

    lag_min = 1.0
    source = "RTIS"

    def poll(self, now_min: float) -> list[dict]:
        cutoff = now_min - self.lag_min
        observations = []
        for row in _load_rows(self.csv_path):
            if row["ts"] <= cutoff:
                observations.append(
                    {
                        "train_id": row["train_id"],
                        "ts": row["ts"],
                        "station": row["station"],
                        "section": row["section"],
                        "delay_min": row["delay_min"],
                        "speed_kmph": row["speed_kmph"],
                        "state": row["state"],
                        "source": self.source,
                        "remark": "",
                    }
                )
        return observations


def poll_all(now_min: float) -> list[dict]:
    """Merge the three mock feeds, keeping the source tag on each record."""
    return (
        NTESAdapter().poll(now_min)
        + COAAdapter().poll(now_min)
        + RTISAdapter().poll(now_min)
    )


class RealAdapter:
    """Stub for the production feed client."""

    def poll(self, now_min: float) -> list[dict]:
        # production swaps in real feeds
        raise NotImplementedError
