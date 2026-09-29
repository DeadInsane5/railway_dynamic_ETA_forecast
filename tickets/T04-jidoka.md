# T04 Jidoka (stop and flag)
**File:** app/jidoka.py. **Budget:** 8 min. **Depends:** T02,T03.
Four behaviours (ADR-0006), each writes to a queue item `{id, kind, train_id, reason, ts, status}` and to the audit registry:
1. Quarantine: invalid observation (from `validate`) held out with flag.
2. Schedule fallback: if System 1 confidence < 0.5 or feed older than 15 min, output `path="fallback"` (schedule + current delay) with a banner flag.
3. Learning line-stop: `check_drift(recent_errors)` freezes learning when mean error rises > 2x baseline.
4. Approval queue: all stopped items listed; `approve(id)` / `dismiss(id)` set status.
**Done:** running the demo dataset yields >= 1 quarantine and >= 1 fallback item.
