"""T04 — Jidoka: stop-the-line and flag (ALL DATA SIMULATED).

Four behaviours (ADR-0006), each producing a queue item
``{id, kind, train_id, reason, ts, status}`` plus an audit-registry entry:

1. Quarantine: an invalid observation (per ``validate``) is held out.
2. Schedule fallback: if System 1 confidence < 0.5 or the feed is older
   than 15 min, flag for schedule-based output (schedule + current delay,
   ``path="fallback"`` — the ETA itself is computed by T09, not here).
3. Learning line-stop: freeze learning when mean error rises > 2x baseline.
4. Approval queue: ``approve`` / ``dismiss`` triage stopped items.

``validate`` is imported from ``app.ontology`` when available; while that
module is being written by a sibling agent, a local stub with identical
semantics (missing fields / delay in [-30, 600] / station order backwards
via S1..S12 index) is used instead.
"""

from __future__ import annotations

import math
import re
import statistics

from app import audit

try:
    from app.ontology import validate
except Exception:  # app/ontology.py missing or mid-write: local stub
    _STATION_RE = re.compile(r"S(\d+)$")

    _REQUIRED_FIELDS = ("train_id", "ts", "station", "delay_min", "state")

    def _station_index(station: object) -> int | None:
        m = _STATION_RE.fullmatch(str(station).strip())
        if not m:
            return None
        idx = int(m.group(1)) - 1
        return idx if 0 <= idx <= 11 else None

    def validate(obs: dict, train_state: dict | None) -> tuple[bool, str]:  # noqa: F811
        """Stub with identical semantics to the T03 contract.

        Checks: no missing fields, delay_min numeric in [-30, 600],
        known station S1..S12, station index not backwards vs
        ``train_state["last_seq"]``.
        """
        for field in _REQUIRED_FIELDS:
            v = obs.get(field)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                return False, f"missing field: {field}"
        try:
            delay = float(obs["delay_min"])
        except (TypeError, ValueError):
            return False, "delay_min not numeric"
        if math.isnan(delay):
            return False, "missing field: delay_min"
        if not -30 <= delay <= 600:
            return False, f"delay {delay} out of range [-30,600]"
        idx = _station_index(obs["station"])
        if idx is None:
            return False, f"unknown station: {obs['station']}"
        if train_state is not None:
            last_seq = train_state.get("last_seq")
            if last_seq is not None and idx < last_seq:
                return False, (
                    f"station order backwards: {obs['station']} "
                    f"after S{last_seq + 1}"
                )
        return True, "ok"


class JidokaQueue:
    """Approval queue for stopped/flagged items."""

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._counter = 1

    def _new_item(self, kind: str, train_id: str | None, reason: str, ts: float) -> dict:
        item = {
            "id": f"J{self._counter}",
            "kind": kind,
            "train_id": train_id,
            "reason": reason,
            "ts": ts,
            "status": "open",
        }
        self._counter += 1
        self._items.append(item)
        return item

    # -- 1. quarantine ----------------------------------------------------
    def quarantine(self, obs: dict, reason: str, ts: float) -> dict:
        item = self._new_item("quarantine", obs.get("train_id"), reason, ts)
        audit.append(
            "jidoka_quarantine",
            {"id": item["id"], "train_id": item["train_id"], "reason": reason},
            path=audit.AUDIT_PATH,
        )
        return item

    def check_observation(
        self, obs: dict, train_state: dict | None, ts: float
    ) -> tuple[bool, str]:
        ok, reason = validate(obs, train_state)
        if not ok:
            self.quarantine(obs, reason, ts)
            return False, reason
        return True, "ok"

    # -- 2. schedule fallback ----------------------------------------------
    def fallback(self, train_id: str, reason: str, ts: float) -> dict:
        item = self._new_item("fallback", train_id, reason, ts)
        audit.append(
            "jidoka_fallback",
            {"id": item["id"], "train_id": train_id, "reason": reason},
            path=audit.AUDIT_PATH,
        )
        return item

    def maybe_fallback(
        self, train_id: str, s1_confidence: float, feed_age_min: float, ts: float
    ) -> dict | None:
        triggers: list[str] = []
        if s1_confidence < 0.5:
            triggers.append(f"s1 confidence {s1_confidence:.2f} < 0.5")
        if feed_age_min > 15:
            triggers.append(f"feed age {feed_age_min:.1f} min > 15 min")
        if not triggers:
            return None
        return self.fallback(train_id, "; ".join(triggers), ts)

    # -- 3. learning line-stop ----------------------------------------------
    def check_drift(self, recent_errors: list[float], baseline_mae: float) -> bool:
        if not recent_errors or not baseline_mae > 0:
            return False
        recent_mae = statistics.mean(recent_errors)
        if recent_mae > 2 * baseline_mae:
            audit.append(
                "freeze",
                {"baseline_mae": baseline_mae, "recent_mae": recent_mae},
                path=audit.AUDIT_PATH,
            )
            return True
        return False

    # -- 4. approval queue ---------------------------------------------------
    def _lookup(self, item_id: str) -> dict:
        for item in self._items:
            if item["id"] == item_id:
                return item
        raise KeyError(item_id)

    def approve(self, item_id: str) -> dict:
        item = self._lookup(item_id)
        item["status"] = "approved"
        audit.append("jidoka_approve", {"id": item_id}, path=audit.AUDIT_PATH)
        return item

    def dismiss(self, item_id: str) -> dict:
        item = self._lookup(item_id)
        item["status"] = "dismissed"
        audit.append("jidoka_dismiss", {"id": item_id}, path=audit.AUDIT_PATH)
        return item

    def pending(self) -> list[dict]:
        return [i for i in self._items if i["status"] == "open"]

    def all_items(self) -> list[dict]:
        return list(self._items)
