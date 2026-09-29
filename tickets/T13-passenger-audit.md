# T13 Passenger view + audit view
**File:** app/static/passenger.html, audit.html (or tabs in index.html). **Budget:** 5 min. **Depends:** T09,T10.
- Passenger: pick a train, show next stations with "expected 14:32 (14:28-14:41)" and on-time chance, plus a language toggle EN / HI / MR (static string dictionary, 10 strings).
- Audit: table from `/api/audit` with hash/sig shortened, a "Verify chain" button calling `/api/audit/verify`, and the explanation for the currently selected ETA.
**Done:** language toggle switches labels; verify shows OK, then bad after a manual file edit.
