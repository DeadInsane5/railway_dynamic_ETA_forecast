# T02 Mock adapters (NTES, COA, RTIS)
**File:** app/adapters.py. **Budget:** 8 min. **Depends:** T01.
Three classes with the same interface `poll(now_min) -> list[Observation]`, replaying `events.csv` up to `now_min`.
- `NTESAdapter`: running status only (train, station, delay), 5-min lag.
- `COAAdapter`: adds `remark` free text and restriction notices.
- `RTISAdapter`: adds `speed_kmph`, 1-min lag.
Merge in `poll_all(now_min)` keeping `source` on each record. Include a `RealAdapter` stub raising NotImplementedError with a comment "production swaps in real feeds".
**Done:** `poll_all(600)` returns merged records with sources tagged.
