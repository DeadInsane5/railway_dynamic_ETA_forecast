"""T09 — FastAPI surface (ALL DATA SIMULATED).

Endpoints per the INDEX contract. Every JSON response includes
``"simulated": true``. Auto-generated docs at ``/docs`` serve as the
end-user API reference. Static dashboard (T12) is served at ``/``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import audit
from app import engine

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Dynamic ETA Forecast Prototype (simulated)",
    description="SIH26028 prototype. ALL DATA SIMULATED. See /docs.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ok(payload: dict, status_code: int = 200) -> JSONResponse:
    payload = {"simulated": True, **payload}
    return JSONResponse(status_code=status_code, content=payload)


# -- trains / eta / matrix ---------------------------------------------------

@app.get("/api/trains")
def api_trains() -> JSONResponse:
    return _ok({"trains": engine.get_trains(), "now": engine.now})


@app.get("/api/eta/{train_id}")
def api_eta(train_id: str) -> JSONResponse:
    eta = engine.get_eta(train_id)
    if eta is None:
        return _ok({"detail": f"unknown train: {train_id}"}, status_code=404)
    return _ok({"eta": eta})


@app.get("/api/matrix")
def api_matrix() -> JSONResponse:
    m = engine.get_matrix()
    return _ok({"now": engine.now, **m})


# -- jidoka ------------------------------------------------------------------

@app.get("/api/jidoka")
def api_jidoka() -> JSONResponse:
    return _ok(
        {
            "now": engine.now,
            "pending": engine.jidoka.pending(),
            "items": engine.jidoka.all_items(),
        }
    )


@app.post("/api/jidoka/{item_id}/approve")
def api_jidoka_approve(item_id: str) -> JSONResponse:
    try:
        item = engine.jidoka.approve(item_id)
    except KeyError:
        return _ok({"detail": f"unknown jidoka item: {item_id}"}, status_code=404)
    return _ok({"item": item})


@app.post("/api/jidoka/{item_id}/dismiss")
def api_jidoka_dismiss(item_id: str) -> JSONResponse:
    try:
        item = engine.jidoka.dismiss(item_id)
    except KeyError:
        return _ok({"detail": f"unknown jidoka item: {item_id}"}, status_code=404)
    return _ok({"item": item})


# -- what-if -----------------------------------------------------------------

class WhatIfBody(BaseModel):
    train_id: str
    station: str
    hold_min: float = 10.0


@app.post("/api/whatif")
def api_whatif(body: WhatIfBody) -> JSONResponse:
    try:
        out = engine.run_whatif(body.train_id, body.station, body.hold_min)
    except StopIteration:
        return _ok({"detail": f"unknown train: {body.train_id}"}, status_code=404)
    except ValueError as exc:
        return _ok({"detail": str(exc)}, status_code=400)
    return _ok({**out, "now": engine.now})


# -- audit / metrics / learning / tick ---------------------------------------

@app.get("/api/audit")
def api_audit(limit: int | None = None) -> JSONResponse:
    ok, bad_seq = audit.verify()
    return _ok(
        {
            "valid": ok,
            "first_bad_seq": bad_seq,
            "entries": audit.read(limit=limit),
        }
    )


@app.get("/api/metrics")
def api_metrics() -> JSONResponse:
    return _ok(engine.get_metrics())


@app.post("/api/learning/run")
def api_learning_run() -> JSONResponse:
    try:
        from app import learning as _learning  # T11; absent until then
    except Exception:
        return _ok(
            {
                "status": "not_implemented",
                "detail": "Learning loop (T11) not yet implemented.",
            },
            status_code=501,
        )
    try:
        out = _learning.run_cycle()
    except Exception as exc:  # e.g. drift line-stop freeze
        return _ok({"status": "error", "detail": str(exc)})
    if isinstance(out, dict):
        return _ok({**out, "status": out.get("status", "ok")})
    return _ok({"status": "ok", "result": out})


class TickBody(BaseModel):
    minutes: float = 5.0


@app.post("/api/tick")
def api_tick(body: TickBody | None = None) -> JSONResponse:
    minutes = body.minutes if body is not None else 5.0
    return _ok(engine.tick(minutes))


# -- static dashboard (T12 builds the real page) ------------------------------

_PLACEHOLDER = """<!doctype html><html><body>
<h1>Dynamic ETA Forecast (Simulated data)</h1>
<p>Dashboard arrives in T12. API is live: see <a href="/docs">/docs</a>.</p>
<p>Try <a href="/api/trains">/api/trains</a> and <a href="/api/eta/T101">/api/eta/T101</a>.</p>
</body></html>"""


def _ensure_static() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    index = STATIC_DIR / "index.html"
    if not index.exists():
        index.write_text(_PLACEHOLDER)


_ensure_static()
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
