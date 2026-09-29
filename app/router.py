"""T08 — Router (rules only, ALL DATA SIMULATED).

Decides the inference path per train::

    route(train_state, s1_out) -> {"path": "cheap"|"full"|"fallback", "rule": str}

Rules, in order (INDEX / T08 contract):

1. Feed stale or quarantined -> ``fallback`` (schedule + current delay;
   the ETA itself is computed by T09, not here).
2. Regime stable and confidence > 0.8 and no restriction ahead -> ``cheap``
   (backbone only).
3. Else -> ``full`` (backbone + residual quantiles + System 1 widening).

``train_state`` is the same dict passed to ``system1.ask`` plus Jidoka
feed flags. Accepted keys (all optional, never raises on odd inputs):

- ``feed_age_min`` (also ``feed_age`` / ``age_min``): minutes since the
  last observation. Stale when > ``STALE_FEED_MIN`` (15, same as
  ``jidoka.maybe_fallback``).
- ``quarantined`` (also ``is_quarantined`` / ``quarantine``): bool.
- ``restriction_ahead`` (also ``has_restriction`` / ``restriction``): bool.

``s1_out`` is the ``system1.ask`` output ``{probs, confidence, ...}``.
Regime = argmax(probs); when ``confidence`` is missing it is derived as
max(probs). Empty/missing probs can never take the cheap path.

A rolling counter of path decisions is kept for the dashboard
("router activity"): see ``counts()`` / ``reset_counts()`` / ``recent()``.
"""

from __future__ import annotations

from collections import deque

STALE_FEED_MIN = 15.0
CHEAP_CONFIDENCE = 0.8

_PATHS = ("cheap", "full", "fallback")

_COUNTS: dict[str, int] = {"cheap": 0, "full": 0, "fallback": 0}
_RECENT: deque = deque(maxlen=200)


def _fnum(v) -> float | None:
    try:
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return False


def _feed_age(state: dict) -> float | None:
    for key in ("feed_age_min", "feed_age", "age_min"):
        if key in state:
            v = _fnum(state.get(key))
            if v is not None:
                return v
    return None


def _quarantined(state: dict) -> bool:
    for key in ("quarantined", "is_quarantined", "quarantine"):
        if key in state:
            return _as_bool(state.get(key))
    # Jidoka-style status flag, e.g. {"jidoka_status": "quarantined"}.
    try:
        status = str(state.get("jidoka_status", "") or "").strip().lower()
    except Exception:
        status = ""
    return status in ("quarantined", "quarantine")


def _restriction_ahead(state: dict) -> bool:
    for key in ("restriction_ahead", "has_restriction", "restriction"):
        if key in state:
            return _as_bool(state.get(key))
    return False


def _regime_and_confidence(s1_out: dict | None) -> tuple[str | None, float]:
    if not isinstance(s1_out, dict):
        return None, 0.0
    probs = s1_out.get("probs")
    if not isinstance(probs, dict) or not probs:
        try:
            conf = float(s1_out.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        return None, conf
    try:
        regime = max(probs, key=lambda k: float(probs[k]))
    except (TypeError, ValueError):
        regime = None
    try:
        conf = float(s1_out.get("confidence", None))
        if conf != conf:  # NaN
            raise ValueError
    except (TypeError, ValueError):
        try:
            conf = max(float(v) for v in probs.values())
        except (TypeError, ValueError):
            conf = 0.0
    return regime, conf


def route(train_state: dict | None, s1_out: dict | None) -> dict:
    """Pick the inference path. Never raises on odd inputs."""
    try:
        state = train_state if isinstance(train_state, dict) else {}
        if not isinstance(s1_out, dict):
            s1_out = {}

        if _quarantined(state):
            result = {"path": "fallback", "rule": "fallback: observation quarantined"}
        else:
            age = _feed_age(state)
            if age is not None and age > STALE_FEED_MIN:
                result = {
                    "path": "fallback",
                    "rule": f"fallback: feed stale (age {age:.1f} min > "
                    f"{STALE_FEED_MIN:.0f} min)",
                }
            else:
                regime, conf = _regime_and_confidence(s1_out)
                restr = _restriction_ahead(state)
                if regime == "stable" and conf > CHEAP_CONFIDENCE and not restr:
                    result = {
                        "path": "cheap",
                        "rule": f"cheap: regime stable, confidence {conf:.2f} > "
                        f"{CHEAP_CONFIDENCE:.1f}, no restriction ahead",
                    }
                else:
                    if regime != "stable":
                        why = f"regime {regime}" if regime else "regime unknown"
                    elif not conf > CHEAP_CONFIDENCE:
                        why = f"confidence {conf:.2f} <= {CHEAP_CONFIDENCE:.1f}"
                    else:
                        why = "restriction ahead"
                    result = {"path": "full", "rule": f"full: {why}"}
    except Exception:
        result = {"path": "full", "rule": "full: router error, safe default"}

    _COUNTS[result["path"]] += 1
    _RECENT.append(result["path"])
    return dict(result)


def counts() -> dict:
    """Copy of the rolling path-decision counters."""
    return dict(_COUNTS)


def recent(n: int = 20) -> list[str]:
    """Last ``n`` path decisions, oldest-first (for dashboard activity)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 20
    return list(_RECENT)[-max(0, n):]


def reset_counts() -> dict:
    """Zero the counters and clear recent history. Returns the new counts."""
    for k in _PATHS:
        _COUNTS[k] = 0
    _RECENT.clear()
    return counts()
