# T05 System 1 stand-in (Laya-shaped)
**File:** app/system1.py. **Budget:** 10 min. **Depends:** T01.
- Features per train: last 3 delay deltas, speed, restriction ahead flag, upstream rake delay.
- Train `LogisticRegression` (or small GradientBoosting) on sim data to predict regime `grows|stable|recovers` (label from next-station delta: > +2, within +-2, < -2).
- Temperature scaling on a validation split; print ECE before and after.
- Interface: `ask(state: dict, question: str, options: list[str]) -> {"probs": {...}, "confidence": max_prob, "latency_ms": x}`. Only the regime question is really implemented; other questions return uniform probs.
- Event reader: keyword rules mapping remarks ("signal failure", "fog", "track") to typed risk `{risk, severity}`. Label it a sample reader.
- Every call is logged with question, options, probs (ADR-0002).
**Done:** `ask()` returns calibrated probs; ECE printed; README notes "stand-in for Laya".
