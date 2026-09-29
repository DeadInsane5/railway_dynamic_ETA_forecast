# T07 Residual quantile GBM + conformal
**File:** app/system2.py (extend). **Budget:** 10 min. **Depends:** T01,T06.
- Target: delta delay of the next station minus backbone prediction (residual). Features: backbone terms, congestion count, hour, section type, regime probs from System 1.
- `GradientBoostingRegressor(loss="quantile")` at alpha 0.1, 0.5, 0.9.
- Split-conformal: on a calibration split compute the adjustment that makes P10-P90 cover 80%; apply it.
- Regime/confidence modulation: widen the band by `1 + (1 - confidence)`.
- Drivers: `learned = residual_p50`. Skip SHAP (use feature importances) if time is short.
- Final output per station: p10/p50/p90 delay, p_on_time from interpolating the quantiles (threshold 5 min).
**Done:** `metrics()` prints MAE of median and coverage per horizon (next station, +2 stations, destination) for the model and for baseline 1 (schedule + current delay).
