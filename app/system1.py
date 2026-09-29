"""T05 — System 1 stand-in (Laya-shaped).

Laya-compatible STAND-IN: a small sklearn classifier (LogisticRegression with
temperature scaling) exposing Laya's ``ask()`` question/options interface.
The real Laya model is out of scope for this prototype. ALL DATA SIMULATED
(trained on ``data/trains.csv`` simulator ground truth).

Sample components (not production grade):
- ``read_remark`` is a SAMPLE keyword reader, not a real NLP extractor.
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REGIMES = ["grows", "stable", "recovers"]
REGIME_QUESTION = "will delay grow, stay stable, or recover at the next station?"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

calls: list = []

_MODEL = None
_TEMP: float = 1.0
_TRAINED: bool = False


# ---------------------------------------------------------------- features

def _features_from_state(state: dict) -> list[float]:
    """Build the 6-dim feature vector [d1, d2, d3, speed, restriction, upstream].

    Never raises: bad/missing inputs fall back to defaults.
    """
    try:
        if not isinstance(state, dict):
            state = {}
        hist = state.get("delay_history", [])
        if not isinstance(hist, (list, tuple)):
            hist = []
        nums: list[float] = []
        for v in hist:
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                nums.append(0.0)
        nums = nums[-4:]  # only the last 4 delays matter for 3 deltas

        def _delta(k: int) -> float:
            # k=1 -> most recent delta hist[-1]-hist[-2]; pad missing with 0.0.
            try:
                if len(nums) > k:
                    return float(nums[-k] - nums[-k - 1])
            except (TypeError, ValueError, IndexError):
                pass
            return 0.0

        d1, d2, d3 = _delta(1), _delta(2), _delta(3)
        try:
            speed = float(state.get("speed_kmph", 0.0) or 0.0)
        except (TypeError, ValueError):
            speed = 0.0
        r = state.get("restriction_ahead", False)
        try:
            if isinstance(r, bool):
                restriction = 1.0 if r else 0.0
            elif isinstance(r, (int, float)):
                restriction = 1.0 if r else 0.0
            else:
                restriction = 0.0
        except Exception:
            restriction = 0.0
        try:
            upstream = float(state.get("upstream_delay", 0.0) or 0.0)
        except (TypeError, ValueError):
            upstream = 0.0
        return [d1, d2, d3, speed, restriction, upstream]
    except Exception:
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------- training data

def _load_rows():
    """Build per-(train, station) feature rows from data/trains.csv.

    History comes from the train's earlier stations; the label comes from the
    NEXT-station delay delta (>+2 grows, <-2 recovers, else stable). Speed is
    joined from data/events.csv, restriction-ahead from data/network.json,
    upstream rake delay from the same rake's earlier service (0.0 otherwise).
    Missing auxiliary files degrade to 0.0 defaults (never raise).
    """
    trains_path = DATA_DIR / "trains.csv"
    with open(trains_path, newline="") as f:
        raw = list(csv.DictReader(f))

    by_train: dict[str, list[dict]] = {}
    for r in raw:
        try:
            by_train.setdefault(r["train_id"], []).append(r)
        except KeyError:
            continue
    for rows in by_train.values():
        rows.sort(key=lambda r: int(float(r.get("seq", 0) or 0)))

    # Departure order = first-station scheduled time.
    def _dep(tid: str) -> float:
        try:
            return float(by_train[tid][0].get("sched_arr", 0.0) or 0.0)
        except (TypeError, ValueError, IndexError):
            return 0.0

    order = sorted(by_train.keys(), key=_dep)

    # Speed lookup: closest-ts match per (train_id, station) from events.csv.
    speed_obs: dict[tuple, list[tuple[float, float]]] = {}
    try:
        with open(DATA_DIR / "events.csv", newline="") as f:
            for e in csv.DictReader(f):
                try:
                    tid, st = e.get("train_id"), e.get("station")
                    if not tid or not st:
                        continue
                    speed_obs.setdefault((tid, st), []).append(
                        (float(e["ts"]), float(e["speed_kmph"]))
                    )
                except (TypeError, ValueError, KeyError):
                    continue
    except (OSError, FileNotFoundError):
        speed_obs = {}

    def _speed(tid: str, st: str, actual_arr: float) -> float:
        cands = speed_obs.get((tid, st))
        if not cands:
            return 0.0
        try:
            return min(cands, key=lambda c: abs(c[0] - actual_arr))[1]
        except (TypeError, ValueError):
            return 0.0

    # Restrictions from network.json.
    restrictions: list[dict] = []
    try:
        net = json.loads((DATA_DIR / "network.json").read_text())
        restrictions = net.get("restrictions", []) or []
    except (OSError, ValueError, AttributeError):
        restrictions = []

    def _restriction_ahead(st: str, nxt: str, t: float) -> float:
        try:
            sec = f"{st}-{nxt}"
            for r in restrictions:
                if r.get("section") == sec and float(r["start_min"]) <= t < float(r["end_min"]):
                    return 1.0
        except (TypeError, ValueError, AttributeError):
            pass
        return 0.0

    # Upstream rake delay: final arrival delay of the same rake's earlier service.
    final_delay: dict[str, float] = {}
    first_train_of_rake: dict[str, str] = {}
    for tid in order:
        rows = by_train[tid]
        try:
            rake = rows[0].get("rake_id", "")
            final = float(rows[-1].get("delay_arr_min", 0.0) or 0.0)
        except (TypeError, ValueError, IndexError):
            continue
        if rake and rake not in first_train_of_rake:
            first_train_of_rake[rake] = tid
            final_delay[rake] = final

    def _upstream(tid: str, rake: str) -> float:
        try:
            if rake and first_train_of_rake.get(rake) != tid:
                return float(final_delay.get(rake, 0.0))
        except (TypeError, ValueError):
            pass
        return 0.0

    feats: list[list[float]] = []
    labels: list[str] = []
    train_of_row: list[str] = []
    for tid in order:
        rows = by_train[tid]
        try:
            delays = [float(r.get("delay_arr_min", 0.0) or 0.0) for r in rows]
        except (TypeError, ValueError):
            continue
        rake = rows[0].get("rake_id", "") if rows else ""
        up = _upstream(tid, rake)
        for i in range(len(rows) - 1):  # need a next station for the label
            hist = delays[: i + 1]
            h = hist[-4:]
            d1 = h[-1] - h[-2] if len(h) >= 2 else 0.0
            d2 = h[-2] - h[-3] if len(h) >= 3 else 0.0
            d3 = h[-3] - h[-4] if len(h) >= 4 else 0.0
            try:
                actual_arr = float(rows[i].get("actual_arr", 0.0) or 0.0)
                sched_dep = float(rows[i].get("sched_dep", 0.0) or 0.0)
            except (TypeError, ValueError):
                actual_arr, sched_dep = 0.0, 0.0
            st = rows[i].get("station", "")
            nxt = rows[i + 1].get("station", "")
            feats.append(
                [
                    d1,
                    d2,
                    d3,
                    _speed(tid, st, actual_arr),
                    _restriction_ahead(st, nxt, sched_dep),
                    up,
                ]
            )
            delta = delays[i + 1] - delays[i]
            labels.append("grows" if delta > 2 else ("recovers" if delta < -2 else "stable"))
            train_of_row.append(tid)
    return np.asarray(feats, dtype=float), labels, order


# ---------------------------------------------------------------- calibration

def _softmax(logits: np.ndarray) -> np.ndarray:
    m = logits - logits.max(axis=-1, keepdims=True)
    e = np.exp(m)
    return e / e.sum(axis=-1, keepdims=True)


def _nll(probs: np.ndarray, y_idx: np.ndarray) -> float:
    eps = 1e-12
    return float(-np.mean(np.log(probs[np.arange(len(y_idx)), y_idx].clip(eps, 1.0))))


def _ece(probs: np.ndarray, y_idx: np.ndarray, n_bins: int = 10) -> float:
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    ece = 0.0
    n = len(y_idx)
    if n == 0:
        return 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        mask = (conf > lo) & (conf <= hi) if b > 0 else (conf >= lo) & (conf <= hi)
        nb = int(mask.sum())
        if nb == 0:
            continue
        acc = float((pred[mask] == y_idx[mask]).mean())
        ece += (nb / n) * abs(acc - float(conf[mask].mean()))
    return float(ece)


def _full_proba(model, X: np.ndarray) -> np.ndarray:
    """predict_proba columns reordered to REGIMES order (missing class -> 0)."""
    P = np.asarray(model.predict_proba(X), dtype=float)
    full = np.zeros((P.shape[0], len(REGIMES)))
    for j, cls in enumerate(model.classes_):
        try:
            full[:, REGIMES.index(str(cls))] = P[:, j]
        except ValueError:
            continue
    return full


def train(verbose: bool = True) -> dict:
    """Fit LogisticRegression on sim ground truth + temperature scaling.

    Chronological split by train departure: first 70% trains fit, next 15%
    validation (temperature line-search in [0.05, 10] minimizing NLL), last
    15% test. Prints 10-bin ECE before/after scaling + test accuracy.
    """
    global _MODEL, _TEMP, _TRAINED

    from sklearn.linear_model import LogisticRegression

    X, labels, order = _load_rows()
    y_idx = np.array([REGIMES.index(l) for l in labels], dtype=int)

    n_trains = len(order)
    n_fit = int(n_trains * 0.70)
    n_val = int(n_trains * 0.15)
    fit_ids = set(order[:n_fit])
    val_ids = set(order[n_fit: n_fit + n_val])

    # Row -> train mapping is positional: _load_rows emits rows train-major in
    # `order` (n_stations - 1 rows per train, last station has no label), so
    # slice arithmetically using per-train row counts from trains.csv.
    idx_fit, idx_val, idx_test = [], [], []
    pos = 0
    with open(DATA_DIR / "trains.csv", newline="") as f:
        n_per_train: dict[str, int] = {}
        for r in csv.DictReader(f):
            n_per_train[r["train_id"]] = n_per_train.get(r["train_id"], 0) + 1
    for tid in order:
        c = max(0, n_per_train.get(tid, 0) - 1)
        if tid in fit_ids:
            idx_fit.extend(range(pos, pos + c))
        elif tid in val_ids:
            idx_val.extend(range(pos, pos + c))
        else:
            idx_test.extend(range(pos, pos + c))
        pos += c

    X_fit, y_fit = X[idx_fit], y_idx[idx_fit]
    X_val, y_val = X[idx_val], y_idx[idx_val]
    X_test, y_test = X[idx_test], y_idx[idx_test]

    model = LogisticRegression(max_iter=1000)
    model.fit(X_fit, [REGIMES[i] for i in y_fit])
    P_val = _full_proba(model, X_val)

    eps = 1e-12
    logits_val = np.log(P_val.clip(eps, 1.0))
    grid = np.exp(np.linspace(np.log(0.05), np.log(10.0), 200))
    nlls = [_nll(_softmax(logits_val / t), y_val) for t in grid]
    T = float(grid[int(np.argmin(nlls))])

    ece_before = _ece(P_val, y_val)
    ece_after = _ece(_softmax(logits_val / T), y_val)
    P_test = _softmax(np.log(_full_proba(model, X_test).clip(eps, 1.0)) / T)
    test_acc = float((P_test.argmax(axis=1) == y_test).mean()) if len(y_test) else 0.0

    _MODEL = model
    _TEMP = T
    _TRAINED = True

    if verbose:
        print("[system1] Laya-compatible STAND-IN (small sklearn LogisticRegression); "
              "real Laya out of scope. ALL DATA SIMULATED.")
        print(f"[system1] n_train={len(idx_fit)} n_val={len(idx_val)} n_test={len(idx_test)} "
              f"T={T:.3f}")
        print(f"[system1] ECE(10-bin) before={ece_before:.4f} after={ece_after:.4f} "
              f"test_acc={test_acc:.4f}")

    return {
        "ece_before": ece_before,
        "ece_after": ece_after,
        "test_acc": test_acc,
        "n_train": len(idx_fit),
        "n_val": len(idx_val),
        "n_test": len(idx_test),
    }


# ---------------------------------------------------------------- ask / reader / logging

def _scaled_proba_dict(feats: list[float]) -> dict:
    P = _full_proba(_MODEL, np.asarray([feats], dtype=float))
    scaled = _softmax(np.log(P.clip(1e-12, 1.0)) / _TEMP)
    return {r: float(scaled[0, i]) for i, r in enumerate(REGIMES)}


def _log_call(question, options: list, probs: dict, confidence: float) -> None:
    calls.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "options": list(options),
            "probs": dict(probs),
            "confidence": confidence,
        }
    )
    if len(calls) % 20 == 0:
        try:
            from app import audit as audit_mod  # lazy: a broken sibling must not break import

            audit_mod.append(
                "system1_decision",
                {
                    "question": question,
                    "options": list(options),
                    "probs": dict(probs),
                    "confidence": confidence,
                },
                path=audit_mod.AUDIT_PATH,
            )
        except Exception:
            pass


def ask(state: dict, question: str, options: list[str]) -> dict:
    """Answer a question with a prob distribution over options.

    The regime question runs the calibrated classifier on features from
    ``state``; anything else returns uniform probs. Never raises on odd inputs.
    """
    t0 = time.perf_counter()
    try:
        opts = list(options) if options is not None else []
    except TypeError:
        opts = []
    try:
        is_regime = (question == REGIME_QUESTION) and (set(opts) == set(REGIMES))
    except Exception:
        is_regime = False

    probs: dict | None = None
    confidence = 0.0
    if is_regime:
        try:
            global _TRAINED
            if not _TRAINED or _MODEL is None:
                train(verbose=False)  # lazy-train so ask() works standalone
            probs = _scaled_proba_dict(_features_from_state(state))
            confidence = float(max(probs.values()))
        except Exception:
            probs = None
            confidence = 0.0

    if probs is None:
        try:
            if len(opts) == 0:
                probs, confidence = {}, 0.0
            else:
                u = 1.0 / len(opts)
                probs = {o: u for o in opts}
                confidence = u
        except Exception:
            probs, confidence = {}, 0.0

    latency_ms = (time.perf_counter() - t0) * 1000.0
    try:
        _log_call(question, opts, probs, confidence)
    except Exception:
        pass
    return {"probs": probs, "confidence": confidence, "latency_ms": latency_ms}


def read_remark(remark: str) -> dict:
    """SAMPLE keyword reader mapping free-text remarks to typed risk."""
    try:
        if not isinstance(remark, str):
            return {"risk": "none", "severity": "low"}
        s = remark.lower()
        if "signal failure" in s:
            return {"risk": "signal", "severity": "high"}
        if "fog" in s:
            return {"risk": "weather", "severity": "medium"}
        if "track" in s:
            return {"risk": "asset", "severity": "medium"}
        return {"risk": "none", "severity": "low"}
    except Exception:
        return {"risk": "none", "severity": "low"}
