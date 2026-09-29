# Handoff: T08–T09 built — T11+ remaining

All data simulated. 37 tests pass (`pytest tests/`). Python 3.12 venv at `.venv/`, run with `.venv/bin/python` from project root. Server: `.venv/bin/python -m uvicorn app.main:app` (verified live on :8123).

## What's built

| Ticket | File(s) | Status |
|---|---|---|
| T08 router | `app/router.py` (181 lines) | done — `route(state, s1_out)→{path, rule}` + `counts()/recent()/reset_counts()` |
| T09 engine | `app/engine.py` (517 lines) | done — clock `now=600`, `tick()`, per-train S1→router→S2, explanations, ETA cache |
| T09 API | `app/main.py` (181 lines) | done — all 10 INDEX endpoints, CORS, `/docs`, static at `/`, every response `"simulated": true` |
| T09 static placeholder | `app/static/index.html` | stub only ("Simulated data", links to /docs) — T12 replaces it |

At `now=600`: 40 trains, paths 33 fallback / 7 full / 0 cheap; Jidoka 1 quarantine + 33 fallbacks open; `/api/audit` 160+ entries, chain verifies.

## Interfaces the next tickets must use (do not change)

- `engine`: `tick(minutes=5)`, `get_eta/get_trains/get_matrix/run_whatif/get_metrics`, `now`, `jidoka`, `etas`. Import runs `reset()` (rebuilds graph + replays to 600; audit log persists).
- ETA per station: `{station, seq, sched_arr/dep, pred_arr/dep, p10/p50/p90 (times), delay_p10/p50/p90, delay, p_on_time, regime, confidence, path, flags, drivers{run,dwell,restriction,recovery,learned}, explanation}`.
- `router.route` never raises; cheap = stable + conf>0.8 + no restriction; fallback = quarantined or feed age>15; else full. Counters live in `app/router.py`, not the engine.
- `POST /api/learning/run` returns 501 stub until T11 provides `app/learning.py::run_cycle()` (main.py imports it lazily — just create the module).

## Key decisions

- Engine dedupes the 3 mock feeds by `(train_id, ts, station)` (RTIS speed + COA remark merged); without this every event ingests 3x. Seen-keys persist across ticks, so re-polling never double-ingests.
- `trains.csv` is read once for timetable columns only; `actual_*`/`delay_arr_min` never touched at runtime (training-only). `predict_full`'s internal schedule read is timetable-only too.
- Full-path times: `pX = pred_arr + (delay_pX − delay)` (preserves backbone arrival/departure relation + learned shift). Cheap/fallback: point times, `p10=p50=p90`.
- Jidoka fallback deduped to one open item per train (else every tick spams 30+ items). Quarantined trains stay `quarantined=True` for the router forever (held out).
- Cheap path ~unreachable on real S1 outputs (max conf ~0.76 on training rows; needs >0.8) — honest, per-spec; T08 proved the branch with mocked input.

## Gotchas

- `data/audit.jsonl` (gitignored) accumulates across runs — now ~117 KB from verification. Delete it for a fresh demo chain; engine recreates it. Same for `data/residual.pkl` (lazy-retrains, ~5 s).
- `system1.ask()` audits every 20th call → each tick adds ~2 `system1_decision` entries (40 trains). Expected, not a leak.
- `GET /api/metrics` prints the residual table to server stdout (from `system2.metrics()`).
- `app/main.py` mounts static at `/` AFTER defining routes; the T12 dashboard just replaces `app/static/index.html`.
- What-if hold at the last station yields `delta_min: 0.0` (no downstream stations) — correct, pick a mid-journey station for the demo.
- Fresh-process rule strings can differ from a ticked process (e.g. T101 "quarantined" vs "stale") — both are fallback; deterministic within a run.

## Remaining (INDEX order)

T11 `app/learning.py` (`run_cycle()` → promote/reject + audit; wire into existing stub) → T12 `app/static/index.html` (dashboard, UX4G) → T13 passenger/audit views → T14 README.
