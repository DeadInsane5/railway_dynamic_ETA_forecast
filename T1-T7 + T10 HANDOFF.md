# Handoff: T01–T07 + T10 built — T08+ remaining

All data simulated. 37 tests pass (`pytest tests/`). Python 3.12 venv at `.venv/`, run with `.venv/bin/python` from project root.

## What's built

| Ticket | File(s) | Status |
|---|---|---|
| T01 sim | `app/sim.py`, `data/network.json`, `data/trains.csv`, `data/events.csv` | done, seed 42, deterministic |
| T02 adapters | `app/adapters.py` | done — `poll_all(600)` → 307 records, 3 sources |
| T03 ontology | `app/ontology.py` | done — exact pinned signatures (see below) |
| T04 jidoka | `app/jidoka.py` | done — 7+ quarantines on demo data |
| T05 system1 | `app/system1.py` | done — LR + temp scaling, ECE 0.160→0.016 |
| T06 backbone | `app/system2.py` (top half) | done — MAE 7.3 |
| T07 residual | `app/system2.py` (bottom half), `data/residual.pkl` | done — coverage 0.72–0.84 |
| T10 audit | `app/audit.py` | done — `data/audit.jsonl` currently ABSENT (fresh chain starts at runtime) |

## Interfaces the next tickets must use (do not change)

- **Observation**: `{train_id, ts, station, section, delay_min, speed_kmph, state, source, remark}`.
- **Train dict** (`system2.trains_from_rows` + `attach_current_state`): `{train_id, name, rake_id, stops:[{station, sched_arr, sched_dep}], cur_seq (-1=unseen), cur_delay, cur_ts}`.
- `ontology`: `build(net, rows)`, `ingest(G, obs)`, `upstream_delay(G, tid)`, `trains_in_section(G, sec, now, window=30)`, `restrictions_on(G, sec, now)`, `validate(obs, state)→(ok, reason)`, `STATION_INDEX`.
- `jidoka.JidokaQueue`: `check_observation`, `maybe_fallback(conf, age)`, `check_drift`, `approve/dismiss/pending/all_items`. Item keys: `{id, kind, train_id, reason, ts, status}`.
- `system1.ask(state, REGIME_QUESTION, REGIMES)` → `{probs, confidence, latency_ms}`; state keys `{train_id, delay_history, speed_kmph, restriction_ahead, upstream_delay}`. NOTE: every 20th `ask()` appends to the audit log — batch/training code should use `_scaled_proba_dict` instead.
- `system2`: `backbone(train, now, G, hold=None)`, `delay_matrix`, `whatif` (label `"scenario"`), `predict_full(train, now, G, regime_probs, confidence)` → per-station `{delay_p10/p50/p90, p_on_time, drivers{run,dwell,restriction,recovery,learned}}`, `train_residual/evaluate/fit_quantiles/calibrate` (reuse for T11), `metrics()`.
- `audit.append(kind, payload)`; kinds in use: `jidoka_*`, `freeze`, `system1_decision`, `model_update`, `promotion`, `rejection`.

## Key decisions

- Restriction cost = `extra_min` consumed directly (sim emits integrated extra minutes; re-deriving `km/v_r−km/v_n` would bias). Noted in `system2` docstring.
- `backbone(now_min)` doubles as congestion cutoff: only events ≤ now count (no future leak in training or runtime).
- Residual GBM = depth-1 stumps (depth-3 overfit: test MAE worse than raw backbone). Honest scoreboard: flat carry-forward still wins at +1 station (1.71 vs 2.77); model wins on longer horizons.
- `predict_full` congestion uses schedule-based proxy (no leakage).

## Gotchas

- `data/` is regenerable except nothing — `audit.jsonl` is gitignored and intentionally deleted; engine (T09) recreates it. Same for `residual.pkl` (lazy-retrains if missing, ~5 s).
- T04 imports `validate` with try/except stub fallback — real import currently works; keep the pattern if files move.
- `system1.ask()` lazy-trains on first call (~2 s); warm it at engine startup.
- `events.csv` has ~2% bad rows (blank ts, delay −45, backwards stations) — adapters skip unparseable; jidoka quarantines invalid.
- Do not read `data/trains.csv` in adapter/engine runtime paths (ground truth is training-only).

## Remaining (INDEX order)

T08 `app/router.py` (`route(state, s1_out)→{path, rule}` + counters) → T09 `app/engine.py` + `app/main.py` (endpoints in INDEX: trains, eta, matrix, jidoka, approve, whatif, audit, learning/run, metrics, tick; every response `"simulated": true`) → T11 `app/learning.py` (reuse T07 fit/evaluate; cycle 1 promote, cycle 2 reject on coverage gate) → T12 `app/static/index.html` → T13 passenger/audit views → T14 README.
