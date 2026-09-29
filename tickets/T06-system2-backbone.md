# T06 System 2 backbone + delay matrix
**File:** app/system2.py. **Budget:** 12 min. **Depends:** T03.
Event-driven backbone `backbone(train, now, ontology) -> per-station list` with additive terms:
`arrival[i+1] = departure[i] + section_runtime + restriction_extra - recovery_slack`, `departure = max(arrival + dwell, sched_dep + inherited_rake_delay)`.
- restriction_extra from ontology restrictions; simple physics: `extra = restricted_km / v_restricted - restricted_km / v_normal`.
- recovery_slack = min(scheduled slack in section, 0.5 * current delay).
- Congestion: if another train is inside the next section within headway (e.g. 6 min), add headway conflict wait.
- Return `drivers = {run, dwell, restriction, recovery}` for the explanation.
- `delay_matrix(now)` returns a stations x trains table of predicted delay minutes (for the heatmap).
- `whatif(train_id, hold_min, station)`: re-run backbone with a hold; return diff vs baseline, labelled `scenario`.
**Done:** backbone-only ETAs computed for all trains; matrix has no NaNs; whatif shifts downstream ETAs.
