# Handoff: T12 built — T13+ remaining

All data simulated. 37 tests pass (`pytest tests/`). Python venv at `.venv/`, run with `.venv/bin/python` from project root. Server: `.venv/bin/python -m uvicorn app.main:app`.

## What's built

| Ticket | File(s) | Status |
|---|---|---|
| T12 dashboard | `app/static/index.html`, `app/static/style.css`, `app/static/app.js` | done — all 7 panels populate from the API; Advance visibly changes ETAs; Jidoka approve/dismiss works; what-if + learning wired |
| T11 learning | `app/learning.py` | done earlier — see `T11 HANDOFF.md` |
| T01–T10 | `app/*.py` | done — see `T9 HANDOFF.md`, `T1-T7 + T10 HANDOFF.md` |

Verified live: `/` serves dashboard (200); 40 trains; tick 605→610 moved T106 ETAs (612.3→609.0, 630.0→632.1, 658.3→661.3); J1 approved via API; what-if hold 10 min at S11 → S12 +10.0 min.

## Interfaces the next tickets must use (do not change)

- Dashboard reads INDEX-contract APIs only: `GET /api/trains`, `/api/eta/{id}`, `/api/matrix`, `/api/jidoka`, `POST /api/jidoka/{id}/approve|dismiss`, `POST /api/whatif {train_id, station, hold_min}`, `GET /api/metrics` (router counts + residual per-horizon MAE/coverage), `POST /api/tick`, `POST /api/learning/run` (~30 s, spinner shown), `GET /api/audit?limit=50` (model-version KPI only).
- UX4G via pinned CDN `https://cdn.ux4g.gov.in/UX4G@3.2.0/` (index.css + ux4g.js + ux4g-custom.js); brand = root token overrides in `style.css` (navy `#1B3A6B` / saffron `#E87722`); `data-theme` on `<html>` drives light/dark.
- Top nav links `./passenger.html` and `./audit.html` (T13 targets) — currently 404.

## Key decisions

- What-if picks the middle *downstream* station (`p50 >= eta.now - 1`, never the last) — mid-array picks hit 400 "already passed" and last-station holds yield delta 0.
- Model-version KPI = latest promotion/rejection cycle from `/api/audit` ("champion · cycle N"), no new endpoint.
- Regime chips: grows=warning, stable=success, recovers=info; path chips: full=primary, fallback=warning, cheap=info. No solid-secondary badges (white-on-saffron contrast 2.96).
- Tab component unused (page links live in navbar); heatmap/ETA bars are token-backed custom CSS documented as gaps G1–G5 in `style.css`.

## Gotchas

- Learning button retrains GBMs (~30 s+); spinner + disabled state shown. Do NOT click it in a throwaway check — it flips `data/learning_state.json` and consumes the PROMOTED→REJECTED demo sequence (reset: `rm data/learning_state.json data/residual.pkl`).
- Test server used port 8129; demo state left ticked (now≈610, J1 approved, 38 Jidoka open). For a clean demo: delete `data/audit.jsonl` (gitignored, engine recreates) and restart (engine resets clock to 600).
- Matrix renders 12 stations × 40 trains (480 cells) in a scroll-x container — fine on desktop, scroll on mobile.
- `doc.ux4g.gov.in/web/ai.txt`/`llms.txt` return Storybook HTML, not text; class/token evidence came from grepping the shipped CDN CSS (details in the T12 completion report).

## Remaining (INDEX order)

T13 `app/static/passenger.html` + `app/static/audit.html` (nav already links them; reuse `style.css` tokens, `ux4g-table`/`ux4g-card`/`ux4g-alert` per the same contract) → T14 README + demo script (note: CDN delivery, navy/saffron overrides, "Simulated data" labelling).
