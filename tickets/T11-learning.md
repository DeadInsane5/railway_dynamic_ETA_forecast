# T11 Learning loop (scripted promotion + rejection)
**File:** app/learning.py. **Budget:** 10 min. **Depends:** T07,T10.
`run_cycle()`: retrain a challenger quantile GBM on the newest window; evaluate champion vs challenger on a rolling holdout: MAE of median per horizon and P10-P90 coverage. Promote only if MAE is lower AND coverage is within +-5 points of 80% (ADR-0019). Log promotion or rejection to audit with metrics.
Demo script: cycle 1 promotes a genuinely better challenger; cycle 2 uses a deliberately degraded challenger (e.g. trained on shuffled labels or over-narrow bands) so it is rejected on the coverage gate. Include `check_drift` hook to Jidoka (line-stop freezes `run_cycle`).
**Done:** `POST /api/learning/run` twice yields one PROMOTED and one REJECTED entry visible in `/api/audit`.
