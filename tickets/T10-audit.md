# T10 Signed audit registry
**File:** app/audit.py. **Budget:** 5 min. **Do early.** Depends: none.
Append-only `data/audit.jsonl`. Each entry: `{seq, ts, kind, payload, prev_hash, hash, sig}` with `hash = sha256(prev_hash + json(payload))` and `sig = HMAC-SHA256(key, hash)` (key from env `AUDIT_KEY`, demo default). Batch flush is optional.
Functions: `append(kind, payload)`, `read(limit)`, `verify() -> (ok, first_bad_seq)`.
Kinds: model_update, promotion, rejection, freeze, override, jidoka_approve, system1_decision (sampled).
**Done:** editing one line of the file makes `verify()` fail at that seq.
