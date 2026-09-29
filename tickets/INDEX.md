# SIH26028 prototype: ticket index (2-hour build)

Goal: a working, presentable demo of the concept. Everything is **simulated** and labelled so (ADR-0016). Keep it simple; cut scope before adding.

## Decisions for this build (override the ADRs only where noted)
- Stack: Python 3.11, FastAPI, uvicorn, pandas, numpy, scikit-learn, networkx. One static HTML frontend (no build step). No Docker needed; `uvicorn app.main:app` is enough.
- System 1 "Laya" is a **stand-in**: small sklearn classifier + temperature scaling behind a Laya-shaped interface (`ask(state, question, options) -> probs`). Label it "Laya-compatible stand-in" in UI and docs. Real Laya is out of scope (deviation from ADR-0002, for time).
- Ontology = networkx graph in memory (deviation from ADR-0007's self-hosted graph store).
- GNN is replaced by a simple congestion feature (trains in section in last 30 min). Conformal is a 10-line split-conformal step. Signing = HMAC-SHA256 hash chain in a JSONL file.
- Skipped: learned router, multilingual reader, what-if UI beyond one button, 10k scale, Kubernetes.

## Layout
```
eta_proto/
  data/            sim output: network.json, trains.csv, events.csv
  app/
    sim.py         T01   adapters.py  T02   ontology.py T03
    jidoka.py      T04   system1.py   T05   system2.py  T06,T07
    router.py      T08   engine.py    T09   main.py     T09
    audit.py       T10   learning.py  T11
    static/        index.html T12; passenger.html, audit.html T13
  README.md        T14
```

## Shared contracts (do not change without updating this file)
Network: 12 stations on one corridor, 11 sections, each with `km`, `sched_run_min`, `type` in {plain, loop, junction}.
Train record: `train_id, name, rake_id, stops:[{station, sched_arr, sched_dep}]`.
Observation (from adapters): `{train_id, ts, station, section, delay_min, speed_kmph, state, source, remark}` where `state` in {running, halted, at_station}.
Regime: one of `grows | stable | recovers`.
ETA output per station: `{station, p10, p50, p90, p_on_time, regime, path, flags:[], drivers:{run, dwell, restriction, recovery, learned}}` with times in minutes-of-day and `delay_*` in minutes.
API: `GET /api/trains`, `GET /api/eta/{train_id}`, `GET /api/matrix`, `GET /api/jidoka`, `POST /api/jidoka/{id}/approve`, `POST /api/whatif`, `GET /api/audit`, `POST /api/learning/run`, `GET /api/metrics`, `POST /api/tick` (advance simulated clock).

## Order and time budget (minutes)
| # | Ticket | Depends | Budget |
|---|---|---|---|
| T01 | Simulator + demo dataset | none | 15 |
| T02 | Mock adapters | T01 | 8 |
| T03 | Ontology + validation | T01 | 8 |
| T04 | Jidoka | T02,T03 | 8 |
| T05 | System 1 stand-in | T01 | 10 |
| T06 | System 2 backbone + delay matrix | T03 | 12 |
| T07 | Residual GBM + conformal | T01,T06 | 10 |
| T08 | Router | T04,T05 | 5 |
| T09 | Engine + explanation + API | T04-T08,T10 | 12 |
| T10 | Signed audit registry | none | 5 (do early) |
| T11 | Learning loop | T07,T10 | 10 |
| T12 | Dashboard (UX4G) | T09 | 15 |
| T13 | Passenger + audit views | T09,T10 | 5 |
| T14 | README + demo script | all | 5 |

Parallelisable: T01/T10 first; then T02-T05 together. Cut order if late: T13 passenger view, then what-if, then GBM (backbone-only is fine).

## Definition of done (whole prototype)
`uvicorn app.main:app` starts; opening `/` shows the dashboard; pressing "Advance" updates ETAs; one Jidoka event appears and can be approved; "Run learning" shows one promotion and one rejection; `/api/audit` shows a verifiable chain; every screen says "Simulated data".
