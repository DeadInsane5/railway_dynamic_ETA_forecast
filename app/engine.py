"""T09 — Engine + explanation (ALL DATA SIMULATED).

Holds the simulated clock ``now`` (minutes-of-day). ``tick()`` replays the
mock adapters up to ``now``, validates + ingests into the ontology, runs
Jidoka checks, then per train: System 1 -> router -> System 2 -> final ETA.
Results are cached for the API layer (``app/main.py``).

Schedule source: ``data/trains.csv`` is read ONCE at startup for timetable
columns only (train_id, name, rake_id, station, seq, sched_arr, sched_dep).
Ground-truth columns (actual_arr/dep, delay_arr_min) are ignored here —
they are training-only for System 1 / residual (per handoff gotcha).

Per-train pipeline (mirrors the T09 ticket)::

    s1_state  = {train_id, delay_history, speed_kmph, restriction_ahead,
                 upstream_delay}
    s1_out    = ask(s1_state, REGIME_QUESTION, REGIMES)  # calibrated probs
    jidoka.maybe_fallback(tid, confidence, feed_age, now)  # schedule flag
    route({**s1_state, feed_age_min, quarantined}, s1_out)  # cheap|full|fallback
    cheap    -> backbone point only (p10=p50=p90)
    full     -> predict_full (residual quantiles + S1 widening)
    fallback -> schedule + current delay, flat, flagged

ETA output per station follows the INDEX contract (plus extras)::

    {station, seq, sched_arr, sched_dep, pred_arr, pred_dep,
     p10, p50, p90, delay_p10, delay_p50, delay_p90, delay, p_on_time,
     regime, confidence, path, flags, drivers{run,dwell,restriction,recovery,
     learned}, explanation}

``p10/p50/p90`` are arrival TIMES (minutes-of-day); ``delay_p*`` are the
matching arrival delays (time minus ``sched_arr``). For the full path the
residual correction is applied as ``pX = pred_arr + (delay_pX - delay)`` so
the backbone arrival/departure relationship is preserved.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app import audit
from app.adapters import poll_all
from app.jidoka import JidokaQueue
from app.ontology import (
    STATION_INDEX,
    build,
    ingest,
    restrictions_on,
    upstream_delay,
)
from app.router import route
from app.system1 import REGIME_QUESTION, REGIMES, ask
from app.system2 import (
    backbone,
    baseline,
    delay_matrix,
    predict_full,
    whatif as _whatif,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
START_NOW_MIN = 600.0
TICK_DEFAULT_MIN = 5.0
UNSEEN_FEED_AGE_MIN = 9999.0
ONTIME_THRESHOLD_MIN = 5.0

_STATIONS_IN_ORDER = sorted(STATION_INDEX, key=STATION_INDEX.get)

# -- module state (reset() rebuilds all of it) -------------------------------
now: float = START_NOW_MIN
G = None
trains: list[dict] = []
jidoka: JidokaQueue = JidokaQueue()
etas: dict[str, dict] = {}
_histories: dict[str, list[float]] = {}
_speeds: dict[str, float] = {}
_last_ts: dict[str, float] = {}
_quarantined: set[str] = set()
_seen_obs: set[tuple] = set()
_validate_state: dict[str, dict] = {}


# -- helpers -----------------------------------------------------------------

def _hhmm(minutes: float) -> str:
    try:
        m = float(minutes) % 1440.0
    except (TypeError, ValueError):
        return "--:--"
    return f"{int(m // 60):02d}:{int(m % 60):02d}"


def _load_schedule_rows() -> tuple[dict, list[dict]]:
    """network.json + timetable columns of trains.csv (no ground truth)."""
    net = json.loads((DATA_DIR / "network.json").read_text())
    sched: list[dict] = []
    with open(DATA_DIR / "trains.csv", newline="") as f:
        for r in csv.DictReader(f):
            try:
                sched.append(
                    {
                        "train_id": r["train_id"],
                        "name": r.get("name", ""),
                        "rake_id": r.get("rake_id", ""),
                        "station": r["station"],
                        "seq": r["seq"],
                        "sched_arr": r["sched_arr"],
                        "sched_dep": r["sched_dep"],
                    }
                )
            except KeyError:
                continue
    return net, sched


def _merge_observations(obs_list: list[dict]) -> list[dict]:
    """Dedupe the 3 mock feeds by (train_id, ts, station).

    NTES/COA/RTIS replay the same underlying event with different lags and
    fields, so without merging the engine would ingest every event 3x.
    Merge prefers RTIS speed and COA remark; source tags are joined.
    """
    merged: dict[tuple, dict] = {}
    for ob in obs_list:
        try:
            key = (str(ob.get("train_id")), float(ob.get("ts")), str(ob.get("station")))
        except (TypeError, ValueError):
            continue
        cur = merged.get(key)
        if cur is None:
            merged[key] = dict(ob)
            continue
        if cur.get("speed_kmph") is None and ob.get("speed_kmph") is not None:
            cur["speed_kmph"] = ob["speed_kmph"]
        if not cur.get("remark") and ob.get("remark"):
            cur["remark"] = ob["remark"]
        if not cur.get("section") and ob.get("section"):
            cur["section"] = ob["section"]
        srcs = set(str(cur.get("source", "")).split("+")) | {str(ob.get("source", ""))}
        cur["source"] = "+".join(sorted(s for s in srcs if s))
    return sorted(merged.values(), key=lambda o: float(o["ts"]))


def _restriction_ahead_for(train: dict) -> bool:
    """True if a restriction is active on the next section at ``now``."""
    try:
        seq = int(train.get("cur_seq", -1))
    except (TypeError, ValueError):
        seq = -1
    nxt = seq + 1 if seq >= 0 else 0
    if nxt >= len(_STATIONS_IN_ORDER) - 1 + 1:
        return False
    # Section carrying the train INTO stops[nxt] (for unseen trains: first).
    idx = max(0, nxt - 1) if seq >= 0 else 0
    if idx >= len(_STATIONS_IN_ORDER) - 1:
        return False
    sec = f"{_STATIONS_IN_ORDER[idx]}-{_STATIONS_IN_ORDER[idx + 1]}"
    try:
        return len(restrictions_on(G, sec, now)) > 0
    except Exception:
        return False


def _explain_station(
    station: str,
    p50_time: float,
    p10_time: float,
    p90_time: float,
    drivers: dict,
    regime: str,
    confidence: float,
    path: str,
    flags: list,
) -> str:
    restr = float(drivers.get("restriction", 0.0) or 0.0)
    rec = float(drivers.get("recovery", 0.0) or 0.0)
    bits = []
    if restr > 0:
        bits.append(f"+{restr:.0f} min from restriction near {station}")
    if rec > 0:
        bits.append(f"-{rec:.0f} min recovery slack")
    detail = ", ".join(bits) if bits else "no restriction or recovery effects"
    return (
        f"Expected {_hhmm(p50_time)} (P10 {_hhmm(p10_time)}, "
        f"P90 {_hhmm(p90_time)}): {detail}, regime: delay {regime} "
        f"({confidence:.0%}), path {path}."
    )


def _build_eta_for(train: dict) -> dict:
    """Run System 1 -> router -> System 2 for one train; cache-ready payload."""
    tid = train["train_id"]
    cur_delay = float(train.get("cur_delay", 0.0) or 0.0)
    cur_ts = float(train.get("cur_ts", 0.0) or 0.0)
    feed_age = (now - cur_ts) if cur_ts > 0 else UNSEEN_FEED_AGE_MIN
    restr_ahead = _restriction_ahead_for(train)

    hist = _histories.get(tid) or [cur_delay]
    s1_state = {
        "train_id": tid,
        "delay_history": hist[-4:],
        "speed_kmph": _speeds.get(tid, 0.0),
        "restriction_ahead": restr_ahead,
        "upstream_delay": upstream_delay(G, tid),
    }
    try:
        s1_out = ask(s1_state, REGIME_QUESTION, list(REGIMES))
    except Exception:
        s1_out = {"probs": {}, "confidence": 0.0}
    probs = s1_out.get("probs") if isinstance(s1_out.get("probs"), dict) else {}
    try:
        regime = max(probs, key=lambda k: float(probs[k])) if probs else "stable"
    except (TypeError, ValueError):
        regime = "stable"
    try:
        confidence = float(s1_out.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    # Jidoka schedule-fallback flag (dedupe: one open fallback per train).
    try:
        has_open_fb = any(
            i["kind"] == "fallback" and i["train_id"] == tid and i["status"] == "open"
            for i in jidoka.all_items()
        )
        if not has_open_fb:
            jidoka.maybe_fallback(tid, confidence, feed_age, now)
    except Exception:
        pass

    rstate = {**s1_state, "feed_age_min": feed_age, "quarantined": tid in _quarantined}
    try:
        routed = route(rstate, s1_out)
    except Exception:
        routed = {"path": "full", "rule": "full: router error, safe default"}
    path = routed.get("path", "full")

    stations_out: list[dict] = []
    if path == "full":
        try:
            preds = predict_full(train, now, G, probs, confidence)
        except Exception:
            preds = backbone(train, now, G)
            path = "cheap"
        else:
            for p in preds:
                seq = p["seq"]
                if seq <= int(train.get("cur_seq", -1)):
                    d = float(p["delay"])
                    t = float(p["pred_arr"])
                    stations_out.append(
                        {
                            "station": p["station"], "seq": seq,
                            "sched_arr": p["sched_arr"], "sched_dep": p["sched_dep"],
                            "pred_arr": t, "pred_dep": float(p["pred_dep"]),
                            "p10": t, "p50": t, "p90": t,
                            "delay": d, "delay_p10": d, "delay_p50": d,
                            "delay_p90": d, "p_on_time": float(p["p_on_time"]),
                            "regime": regime, "confidence": round(confidence, 3),
                            "path": path, "flags": list(p.get("flags", [])),
                            "drivers": dict(p.get("drivers", {})),
                            "explanation": (
                                f"Observed {_hhmm(t)} at {p['station']} "
                                f"(delay {d:.0f} min), regime: delay {regime} "
                                f"({confidence:.0%}), path {path}."
                            ),
                        }
                    )
                    continue
                delay_pt = float(p["delay"])
                for k in ("delay_p10", "delay_p50", "delay_p90"):
                    p[k] = float(p.get(k, delay_pt))
                base_t = float(p["pred_arr"])
                t10 = round(base_t + (p["delay_p10"] - delay_pt), 1)
                t50 = round(base_t + (p["delay_p50"] - delay_pt), 1)
                t90 = round(base_t + (p["delay_p90"] - delay_pt), 1)
                stations_out.append(
                    {
                        "station": p["station"], "seq": seq,
                        "sched_arr": p["sched_arr"], "sched_dep": p["sched_dep"],
                        "pred_arr": base_t, "pred_dep": float(p["pred_dep"]),
                        "p10": t10, "p50": t50, "p90": t90,
                        "delay": delay_pt, "delay_p10": p["delay_p10"],
                        "delay_p50": p["delay_p50"], "delay_p90": p["delay_p90"],
                        "p_on_time": float(p["p_on_time"]),
                        "regime": regime, "confidence": round(confidence, 3),
                        "path": path, "flags": list(p.get("flags", [])),
                        "drivers": dict(p.get("drivers", {})),
                        "explanation": _explain_station(
                            p["station"], t50, t10, t90, p["drivers"],
                            regime, confidence, path, p.get("flags", []),
                        ),
                    }
                )
            etas[tid] = _finish_train_payload(train, path, routed, regime,
                                              confidence, probs, stations_out)
            return etas[tid]

    if path == "cheap":
        preds = backbone(train, now, G)
        for p in preds:
            t = float(p["pred_arr"])
            d = round(t - float(p["sched_arr"]), 1)
            stations_out.append(
                {
                    "station": p["station"], "seq": p["seq"],
                    "sched_arr": p["sched_arr"], "sched_dep": p["sched_dep"],
                    "pred_arr": t, "pred_dep": float(p["pred_dep"]),
                    "p10": t, "p50": t, "p90": t,
                    "delay": d, "delay_p10": d, "delay_p50": d, "delay_p90": d,
                    "p_on_time": 1.0 if d <= ONTIME_THRESHOLD_MIN else 0.0,
                    "regime": regime, "confidence": round(confidence, 3),
                    "path": path, "flags": list(p.get("flags", [])),
                    "drivers": dict(p.get("drivers", {})),
                    "explanation": _explain_station(
                        p["station"], t, t, t, p["drivers"],
                        regime, confidence, path, p.get("flags", []),
                    ),
                }
            )
    else:  # fallback: schedule + current delay, flat, banner flag
        base = {b["seq"]: b for b in baseline(train)}
        preds = backbone(train, now, G)
        by_seq = {p["seq"]: p for p in preds}
        for i, s in enumerate(train["stops"]):
            t = round(float(s["sched_arr"]) + cur_delay, 1)
            d = round(cur_delay, 1)
            flags = ["fallback"]
            if i <= int(train.get("cur_seq", -1)):
                flags = (["observed"] if i == int(train.get("cur_seq", -1))
                         else ["backfilled"]) + ["fallback"]
            _ = base.get(i, {}), by_seq.get(i, {})
            stations_out.append(
                {
                    "station": s["station"], "seq": i,
                    "sched_arr": float(s["sched_arr"]),
                    "sched_dep": float(s["sched_dep"]),
                    "pred_arr": t, "pred_dep": round(float(s["sched_dep"]) + cur_delay, 1),
                    "p10": t, "p50": t, "p90": t,
                    "delay": d, "delay_p10": d, "delay_p50": d, "delay_p90": d,
                    "p_on_time": 1.0 if d <= ONTIME_THRESHOLD_MIN else 0.0,
                    "regime": regime, "confidence": round(confidence, 3),
                    "path": "fallback", "flags": flags,
                    "drivers": {"run": 0.0, "dwell": 0.0, "restriction": 0.0,
                                "recovery": 0.0, "learned": 0.0},
                    "explanation": (
                        f"Schedule fallback at {s['station']}: expected "
                        f"{_hhmm(t)} (current delay {d:.0f} min carried "
                        f"forward), regime: delay {regime} "
                        f"({confidence:.0%}), path fallback."
                    ),
                }
            )
        path = "fallback"

    etas[tid] = _finish_train_payload(train, path, routed, regime,
                                      confidence, probs, stations_out)
    return etas[tid]


def _finish_train_payload(train, path, routed, regime, confidence, probs, stations_out):
    cur_seq = int(train.get("cur_seq", -1))
    nxt = next((s for s in stations_out if s["seq"] > cur_seq), stations_out[-1] if stations_out else None)
    summary = nxt["explanation"] if nxt else "No stations."
    return {
        "train_id": train["train_id"],
        "name": train.get("name", ""),
        "rake_id": train.get("rake_id", ""),
        "now": round(now, 1),
        "simulated": True,
        "path": path,
        "rule": routed.get("rule", ""),
        "regime": regime,
        "confidence": round(confidence, 3),
        "regime_probs": {k: round(float(probs.get(k, 0.0)), 4) for k in REGIMES} if probs else {},
        "explanation": summary,
        "stations": stations_out,
    }


def _recompute_all_etas() -> None:
    from app.system2 import attach_current_state

    attach_current_state(trains, G)
    for t in trains:
        _build_eta_for(t)


# -- lifecycle ---------------------------------------------------------------

def reset(now_min: float = START_NOW_MIN) -> dict:
    """Rebuild graph, queues and caches; ingest up to ``now_min``."""
    global now, G, trains, jidoka, etas
    global _histories, _speeds, _last_ts, _quarantined, _seen_obs, _validate_state
    now = float(now_min)
    net, sched = _load_schedule_rows()
    G = build(net, sched)
    from app.system2 import trains_from_rows

    trains = trains_from_rows(sched)
    jidoka = JidokaQueue()
    etas = {}
    _histories, _speeds, _last_ts = {}, {}, {}
    _quarantined, _seen_obs, _validate_state = set(), set(), {}
    try:  # warm System 1 so first tick is fast (~2 s once)
        from app.system1 import train as _s1_train

        _s1_train(verbose=False)
    except Exception:
        pass
    _ingest_up_to(now)
    _recompute_all_etas()
    return {"now": now, "trains": len(trains)}


def _ingest_up_to(upto: float) -> dict:
    """Poll, validate and ingest everything new with ts <= ``upto``."""
    fresh = [o for o in _merge_observations(poll_all(upto))
             if (str(o.get("train_id")), float(o["ts"]), str(o.get("station"))) not in _seen_obs]
    ingested = quarantined = 0
    for ob in fresh:
        key = (str(ob.get("train_id")), float(ob["ts"]), str(ob.get("station")))
        _seen_obs.add(key)
        tid = str(ob.get("train_id"))
        vst = _validate_state.get(tid)
        try:
            ok, _ = jidoka.check_observation(ob, vst, float(ob["ts"]))
        except Exception:
            ok = False
        if not ok:
            quarantined += 1
            _quarantined.add(tid)
            continue
        try:
            ingest(G, ob)
        except Exception:
            continue
        ingested += 1
        try:
            seq = STATION_INDEX.get(ob.get("station"))
        except Exception:
            seq = None
        if seq is not None:
            _validate_state[tid] = {"last_seq": seq, "last_ts": float(ob["ts"])}
        try:
            _histories.setdefault(tid, []).append(float(ob.get("delay_min", 0.0)))
            _last_ts[tid] = float(ob["ts"])
            if ob.get("speed_kmph") is not None:
                _speeds[tid] = float(ob["speed_kmph"])
        except (TypeError, ValueError):
            pass
    return {"ingested": ingested, "quarantined": quarantined}


def tick(minutes: float = TICK_DEFAULT_MIN) -> dict:
    """Advance the simulated clock and refresh all ETAs."""
    global now
    try:
        step = float(minutes)
    except (TypeError, ValueError):
        step = TICK_DEFAULT_MIN
    now = round(now + step, 1)
    stats = _ingest_up_to(now)
    _recompute_all_etas()
    return {"now": now, "simulated": True, **stats,
            "trains": len(trains), "jidoka_open": len(jidoka.pending())}


# -- read API used by app/main.py --------------------------------------------

def get_trains() -> list[dict]:
    out = []
    for t in trains:
        cur_seq = int(t.get("cur_seq", -1))
        stops = t.get("stops", [])
        nxt = stops[cur_seq + 1]["station"] if 0 <= cur_seq + 1 < len(stops) else None
        out.append(
            {
                "train_id": t["train_id"], "name": t.get("name", ""),
                "rake_id": t.get("rake_id", ""), "cur_seq": cur_seq,
                "cur_delay": round(float(t.get("cur_delay", 0.0)), 1),
                "cur_ts": float(t.get("cur_ts", 0.0) or 0.0),
                "next_station": nxt,
                "path": (etas.get(t["train_id"], {}).get("path")),
                "regime": (etas.get(t["train_id"], {}).get("regime")),
            }
        )
    return out


def get_eta(train_id: str) -> dict | None:
    return etas.get(str(train_id))


def get_matrix() -> dict:
    return delay_matrix(now, trains, G)


def run_whatif(train_id: str, station: str, hold_min: float) -> dict:
    return _whatif(str(train_id), float(hold_min), str(station), trains, G, now)


def get_metrics() -> dict:
    from app import router as _router
    from app.system2 import metrics as _resid_metrics

    try:
        resid = _resid_metrics()
    except Exception as exc:
        resid = {"error": str(exc)}
    return {"now": now, "router": _router.counts(), "residual": resid}


# Initialise on import so ``uvicorn app.main:app`` serves immediately.
reset()
