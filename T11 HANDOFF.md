# Handoff: T11 built — T12+ remaining

All data simulated. 37 tests pass (`pytest tests/`). Python 3.12 venv at `.venv/`, run with `.venv/bin/python` from project root. Server: `.venv/bin/python -m uvicorn app.main:app`.

## What's built

| Ticket | File(s) | Status |
|---|---|---|
| T11 learning | `app/learning.py` (~200 lines) | done — `run_cycle(degraded=None)` → PROMOTED/REJECTED dict + audit; wired into existing `POST /api/learning/run` stub (no `main.py` change needed) |
| T10 audit | `app/audit.py` | done earlier — chain verifies `(True, None)` |
| T01–T09 | `app/sim.py`, `adapters.py`, `ontology.py`, `jidoka.py`, `system1.py`, `system2.py`, `router.py`, `engine.py`, `main.py` | done — see `T9 HANDOFF.md` |

Verified fresh: `POST /api/learning/run` → 200 PROMOTED (cycle 1), then 200 REJECTED (cycle 2); `/api/audit` shows `model_update`/`promotion`/`rejection`, chain valid.

## Interfaces the next tickets must use (do not change)

- `learning.run_cycle(degraded=None)` — `None` (API default) alternates via `data/learning_state.json` (odd = good challenger, even = degraded); explicit `True/False` forces a branch. Returns `{"status": "PROMOTED"|"REJECTED", "audit_seq", "cycle", "branch", "champion", "challenger", "gate", ...}`; raises `RuntimeError` on Jidoka line-stop freeze (`main.py` surfaces it as error status).
- Promotion gate (ADR-0019): mean MAE over horizons strictly lower than champion AND mean P10–P90 coverage within 80% ± 5 pts. Promotion overwrites `data/residual.pkl` and hot-swaps `system2._resid_bundle`; rejection leaves the champion untouched.
- `engine`: unchanged (`tick()`, `get_eta/get_trains/get_matrix/run_whatif/get_metrics`, `now`, `jidoka`, `etas`). `app/main.py` mounts static at `/` AFTER routes; T12 just replaces `app/static/index.html`.

## Key decisions

- Good challenger = depth-1 stumps with `n_estimators=300, min_samples_leaf=20, lr=0.05` (small search result: mean MAE 2.837 vs champion 2.852, coverage ~0.81 — wins on merit, not scripted).
- Degraded challenger = normal median but over-narrow bands (adjustment forced to 0, half-widths × 0.15) → coverage ~0.14, rejected on the coverage gate per the ticket (MAE reason also listed when it fails).
- Drift hook uses a fresh `JidokaQueue().check_drift(...)` (not the engine's queue) to avoid side effects on the live pending list; `freeze` is audited inside `check_drift`.
- No T11 test added deliberately: each cycle retrains GBMs (~30 s+) vs the current 4.4 s suite; verification was via direct calls + API TestClient.

## Gotchas

- Repo left in re-demoable state: original champion in `data/residual.pkl`, `data/learning_state.json` deleted — the next two `POST /api/learning/run` calls reproduce PROMOTED → REJECTED. To re-demo after that: `rm data/learning_state.json data/residual.pkl` (both regenerable; residual lazy-retrains, ~5 s).
- After a promotion, the next *good* cycle will likely REJECT (challenger ties the new champion) — honest behavior, not a bug. The alternating pattern only holds for the first two fresh cycles.
- `data/audit.jsonl` (gitignored) accumulates across runs and already contains older promotion/rejection entries — chain still valid; check the latest entries for the current demo.
- `run_cycle` rebuilds the residual dataset each call (System 1 warm-up ~2 s first time, GBM training dominates).

## Remaining (INDEX order)

T12 `app/static/index.html` (dashboard, UX4G) → T13 passenger/audit views (`passenger.html`, `audit.html`) → T14 README + demo script.
