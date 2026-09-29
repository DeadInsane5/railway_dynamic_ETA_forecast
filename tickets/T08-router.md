# T08 Router (rules only)
**File:** app/router.py. **Budget:** 5 min. **Depends:** T04,T05.
`route(train_state, s1_out) -> {"path": "cheap"|"full"|"fallback", "rule": "..."}`
Rules in order: feed stale or quarantined -> fallback; regime stable and confidence > 0.8 and no restriction ahead -> cheap (backbone only); else full (backbone + residuals + System 1 widening).
Keep a rolling counter of path decisions for the dashboard ("router activity").
**Done:** counters show all three paths used on the demo data.
