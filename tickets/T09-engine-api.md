# T09 Engine, explanation, API
**File:** app/engine.py, app/main.py. **Budget:** 12 min. **Depends:** T04-T08, T10.
Engine holds simulated clock `now`. `tick(minutes=5)`: poll adapters, validate, ingest to ontology, Jidoka checks, per train: System 1 -> router -> System 2 -> final ETA. Cache results.
Explanation: one plain sentence per ETA, e.g. "Expected 14:32 (P10 14:28, P90 14:41): +6 min from restriction near S5, -3 min recovery slack, regime: delay grows (72%)." Include the driver dict.
Implement endpoints listed in INDEX contract. Serve `app/static/` at `/`. Every response includes `"simulated": true`. Enable CORS. Auto-generated docs at `/docs` serve as the end-user API reference.
**Done:** `curl /api/eta/T101` returns per-station distributions plus explanation; `/api/tick` changes results.
