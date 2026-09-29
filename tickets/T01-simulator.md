# T01 Simulator + demo dataset
**File:** app/sim.py, outputs to data/. **Budget:** 15 min. **Depends:** none.
Generate with a fixed seed:
- `network.json`: 12 stations, 11 sections (see INDEX contract).
- 40 trains over one day, each with a `rake_id`; some rakes serve two trains in sequence (late rake 1 passes delay to service 2, ADR-0021).
- Per-train per-station actual delay via a simple process: `delay[i+1] = delay[i] + noise + restriction_effect + congestion_effect - recovery`. Noise heavy-tailed (lognormal or student-t). 2-3 random speed restrictions (section, start, end, extra minutes).
- `events.csv`: minute-by-minute observations per train per station (delay, speed, state, remark). Insert ~2% bad records (negative delay, backwards station order, missing fields) and 3 free-text remarks (e.g. "signal failure near S5").
- Ground truth kept in `trains.csv` for training/eval; adapters must not expose future rows.
**Done:** `python -m app.sim` writes the 3 files; a printed summary shows mean/95th-pct delay.
