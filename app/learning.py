"""T11 — Learning loop: scripted promotion + rejection (ALL DATA SIMULATED).

``run_cycle()`` retrains a challenger quantile GBM and evaluates champion
vs challenger on a rolling holdout (the chronological test split from
``system2.split_residual_rows``): MAE of the median per horizon and P10-P90
coverage. Promotion requires BOTH (ADR-0019):

- mean MAE (over horizons) strictly lower than the champion, AND
- mean P10-P90 coverage within 80% +- 5 points (i.e. [0.75, 0.85]).

Demo script (per the T11 ticket):

- Cycle 1 (odd) trains a genuinely better challenger: same depth-1 stumps
  but ``n_estimators=300, min_samples_leaf=20, learning_rate=0.05``. On the
  demo dataset this yields mean MAE 2.837 vs champion 2.852 with mean
  coverage ~0.81, so it promotes on merit.
- Cycle 2 (even) trains a deliberately degraded challenger: identical
  median but over-narrow bands (adjustment forced to 0 + half-widths shrunk
  to 15%), so coverage collapses well below 0.75 and it is rejected on the
  coverage gate.

Which cycle runs is tracked in ``data/learning_state.json`` (``{"cycles"}``
counter), so two fresh ``POST /api/learning/run`` calls yield one PROMOTED
then one REJECTED entry in ``/api/audit``. To re-demo from scratch, delete
``data/learning_state.json`` and ``data/residual.pkl`` (both regenerable).

A ``check_drift`` hook to Jidoka runs first: if mean absolute error on the
holdout exceeds 2x the baseline MAE, a ``freeze`` entry is audited (inside
``JidokaQueue.check_drift``) and ``run_cycle`` raises ``RuntimeError``
(line-stop); ``app/main.py`` surfaces that as an error status.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

from app import audit
from app.jidoka import JidokaQueue
from app.system2 import (
    RESID_ALPHAS,
    RESID_FEATURES,
    RESID_PATH,
    calibrate,
    evaluate,
    fit_quantiles,
    load_residual,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_PATH = DATA_DIR / "learning_state.json"

COVERAGE_TARGET = 0.80
COVERAGE_TOL = 0.05
HORIZONS = ("next", "plus2", "dest", "mid")
DEGRADED_SHRINK = 0.15  # over-narrow bands for the scripted rejection


# -- cycle state -----------------------------------------------------------

def _next_cycle() -> int:
    """1-based cycle number for this run (state file counts completed runs)."""
    try:
        done = int(json.loads(STATE_PATH.read_text()).get("cycles", 0))
    except (FileNotFoundError, ValueError, AttributeError, TypeError):
        done = 0
    return done + 1


def _mark_cycle_done() -> int:
    try:
        done = int(json.loads(STATE_PATH.read_text()).get("cycles", 0))
    except (FileNotFoundError, ValueError, AttributeError, TypeError):
        done = 0
    done += 1
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"cycles": done}))
    return done


# -- challenger training ----------------------------------------------------

def _fit_good(rows_train: list[dict]):
    """Genuinely-better challenger hyperparams (found by small search).

    Depth-1 stumps like the champion, but n_estimators=300,
    min_samples_leaf=20, learning_rate=0.05: mean MAE 2.837 vs 2.852 on the
    demo data with coverage inside the gate.
    """
    from sklearn.ensemble import GradientBoostingRegressor

    X = np.asarray([r["feat"] for r in rows_train])
    y = np.asarray([r["target"] for r in rows_train])
    models = {}
    for a in RESID_ALPHAS:
        m = GradientBoostingRegressor(
            loss="quantile", alpha=a, n_estimators=300, max_depth=1,
            learning_rate=0.05, min_samples_leaf=20,
            subsample=0.8, random_state=7,
        )
        m.fit(X, y)
        models[a] = m
    importances = dict(zip(RESID_FEATURES, models[0.5].feature_importances_))
    return {"models": models, "importances": importances}


def _evaluate_narrow(models: dict, rows_test: list[dict]) -> dict:
    """Evaluate with over-narrow bands (degraded: coverage must collapse)."""
    X = np.asarray([r["feat"] for r in rows_test])
    y = np.asarray([r["target"] for r in rows_test])
    q10_raw = models[0.1].predict(X)
    q50 = models[0.5].predict(X)
    q90_raw = models[0.9].predict(X)
    # adjustment forced to 0 and half-widths shrunk: deliberately over-narrow.
    q10 = q50 - (q50 - q10_raw) * DEGRADED_SHRINK
    q90 = q50 + (q90_raw - q50) * DEGRADED_SHRINK
    out = {}
    for h in sorted({r["horizon"] for r in rows_test}):
        idx = np.array([i for i, r in enumerate(rows_test) if r["horizon"] == h])
        resid = y[idx] - q50[idx]
        mae = float(np.mean(np.abs(resid)))
        cov = float(np.mean((y[idx] >= q10[idx]) & (y[idx] <= q90[idx])))
        errs = [abs(rows_test[int(i)]["target"] + rows_test[int(i)]["feat"][0]
                    - rows_test[int(i)]["baseline"]) for i in idx]
        out[h] = {"mae_model": mae, "coverage": cov,
                  "mae_baseline": float(np.mean(errs)), "n": int(len(idx))}
    return out


def _means(metrics: dict) -> tuple[float, float]:
    hs = [h for h in HORIZONS if h in metrics] or sorted(metrics)
    return (float(np.mean([metrics[h]["mae_model"] for h in hs])),
            float(np.mean([metrics[h]["coverage"] for h in hs])))


# -- main entry --------------------------------------------------------------

def run_cycle(degraded: bool | None = None) -> dict:
    """Run one learning cycle; promote or reject the challenger.

    ``degraded=None`` (default, used by the API) alternates per the state
    file: odd cycles train the good challenger, even cycles the degraded
    one. Pass ``degraded=True/False`` explicitly to force a branch.
    """
    from app.system2 import build_residual_dataset, split_residual_rows

    cycle = _next_cycle()
    if degraded is None:
        degraded = (cycle % 2 == 0)

    rows = build_residual_dataset()
    rows_train, rows_cal, rows_test = split_residual_rows(rows)

    champ = load_residual()
    champ_metrics = evaluate(champ["models"], champ["adjustment"], rows_test)
    champ_mae, champ_cov = _means(champ_metrics)
    base_mae = float(np.mean([champ_metrics[h]["mae_baseline"]
                              for h in champ_metrics]))

    # Drift line-stop hook: recent = champion abs median errors on holdout.
    Xte = np.asarray([r["feat"] for r in rows_test])
    yte = np.asarray([r["target"] for r in rows_test])
    recent_errors = [float(e) for e in
                     np.abs(yte - champ["models"][0.5].predict(Xte))]
    if JidokaQueue().check_drift(recent_errors, base_mae):
        _mark_cycle_done()
        raise RuntimeError(
            f"learning frozen by Jidoka line-stop "
            f"(recent MAE {np.mean(recent_errors):.2f} > 2x baseline {base_mae:.2f})"
        )

    if degraded:
        fit = fit_quantiles(rows_train)  # median is fine; bands are sabotaged
        chall_metrics = _evaluate_narrow(fit["models"], rows_test)
        chall_mae, chall_cov = _means(chall_metrics)
        branch = "degraded (over-narrow bands, adjustment=0, shrink=0.15)"
        adjustment = 0.0
        bundle = None
    else:
        fit = _fit_good(rows_train)
        adjustment = calibrate(fit["models"], rows_cal)
        chall_metrics = evaluate(fit["models"], adjustment, rows_test)
        chall_mae, chall_cov = _means(chall_metrics)
        branch = "good (n_estimators=300, leaf=20, lr=0.05)"
        bundle = {"models": fit["models"], "adjustment": adjustment,
                  "features": RESID_FEATURES, "importances": fit["importances"],
                  "metrics": chall_metrics,
                  "n_train": len(rows_train), "n_cal": len(rows_cal),
                  "n_test": len(rows_test)}

    mae_wins = chall_mae < champ_mae
    cov_ok = (COVERAGE_TARGET - COVERAGE_TOL
              <= chall_cov <= COVERAGE_TARGET + COVERAGE_TOL)
    promote = mae_wins and cov_ok

    summary = {
        "cycle": cycle,
        "branch": branch,
        "champion": {"mean_mae": round(champ_mae, 3),
                     "mean_coverage": round(champ_cov, 3)},
        "challenger": {"mean_mae": round(chall_mae, 3),
                       "mean_coverage": round(chall_cov, 3),
                       "adjustment": round(float(adjustment), 3),
                       "per_horizon": chall_metrics},
        "gate": {"mae_lower": bool(mae_wins),
                 "coverage_within_80pm5": bool(cov_ok)},
    }

    if promote:
        assert bundle is not None
        RESID_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RESID_PATH, "wb") as f:
            pickle.dump(bundle, f)
        import app.system2 as _s2
        _s2._resid_bundle = bundle
        audit.append("model_update", {"cycle": cycle, **summary["challenger"]})
        entry = audit.append("promotion", summary)
        _mark_cycle_done()
        return {"status": "PROMOTED", "audit_seq": entry["seq"], **summary}

    reasons = []
    if not mae_wins:
        reasons.append(f"MAE {chall_mae:.3f} not lower than champion {champ_mae:.3f}")
    if not cov_ok:
        reasons.append(
            f"coverage {chall_cov:.3f} outside "
            f"[{COVERAGE_TARGET - COVERAGE_TOL:.2f}, "
            f"{COVERAGE_TARGET + COVERAGE_TOL:.2f}]")
    summary["reasons"] = reasons
    entry = audit.append("rejection", summary)
    _mark_cycle_done()
    return {"status": "REJECTED", "audit_seq": entry["seq"], **summary}
