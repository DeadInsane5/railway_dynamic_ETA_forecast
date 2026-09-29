# T03 Very basic ontology + validation
**File:** app/ontology.py. **Budget:** 8 min. **Depends:** T01.
networkx DiGraph with node types Station, Section, Train, Rake, Restriction, Event and edges `NEXT`, `RUNS_ON`, `FORMS`, `FEEDS_RAKE` (rake -> next service), `AFFECTS`.
Functions: `build(network, trains)`, `ingest(obs)` adds Event nodes and updates train state, `upstream_delay(train_id)` (rake inheritance), `trains_in_section(section, window)` (congestion), `restrictions_on(section, now)`.
Validation `validate(obs, train_state) -> (ok, reason)`: state-machine check (station index must not go backwards, delay in [-30, 600], no missing fields).
**Done:** unit check that a backwards-station record fails validation with a reason string.
