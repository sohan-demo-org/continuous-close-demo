"""FastAPI dashboard: Scoreboard, Mismatches, Accruals, Trend."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config, reporting
from ..db import connect

# DuckDB refuses mixed read-only/read-write handles on one file inside a process, and a page
# rendered mid-pipeline would show a half-written snapshot, so reads and actions take one lock.
DB_LOCK = threading.Lock()

HERE = Path(__file__).parent
app = FastAPI(title="Continuous Close")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def money(x, decimals: int = 0) -> str:
    if x is None:
        return "-"
    s = f"{abs(x):,.{decimals}f}"
    return f"-${s}" if x < 0 else f"${s}"


def signed(x, decimals: int = 0) -> str:
    if x is None:
        return "-"
    return ("+" if x >= 0 else "-") + f"${abs(x):,.{decimals}f}"


def compact(x) -> str:
    if x is None:
        return "-"
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1_000_000:
        return f"{sign}${x / 1_000_000:.2f}M"
    if x >= 1_000:
        return f"{sign}${x / 1_000:.0f}k"
    return f"{sign}${x:,.0f}"


templates.env.filters.update(money=money, signed=signed, compact=compact,
                             dt=lambda d, f="%b %-d": d.strftime(f) if d else "-",
                             tojson=lambda v: json.dumps(v, default=str))


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    with DB_LOCK:
        return _render(request, name, **ctx)


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    con = connect(config.DB_PATH)
    try:
        state = reporting.current_state(con)
        base = {"request": request, "hdr": reporting.header(con, state), "view": name,
                "stats": reporting.recon_stats(con, state.as_of),
                "accrual_total": sum(a["accrual_amount"] for a in reporting.accruals(con, state.as_of))}
        extra = {k: (v(con, state) if callable(v) else v) for k, v in ctx.items()}
        return templates.TemplateResponse(request, f"{name}.html", {**base, **extra})
    finally:
        con.close()


@app.get("/", response_class=HTMLResponse)
def scoreboard(request: Request):
    return render(request, "scoreboard", sb=lambda con, s: reporting.scoreboard(con, s.as_of),
                  trend=lambda con, s: reporting.trend(con, s.as_of),
                  mismatches=lambda con, s: reporting.mismatches(con, s.as_of)[:5])


@app.get("/mismatches", response_class=HTMLResponse)
def mismatches(request: Request):
    return render(request, "mismatches", mismatches=lambda con, s: reporting.mismatches(con, s.as_of))


@app.get("/accruals", response_class=HTMLResponse)
def accruals(request: Request):
    return render(request, "accruals", accruals=lambda con, s: reporting.accruals(con, s.as_of),
                  pos=lambda con, s: reporting.open_pos(con, s.as_of))


@app.get("/trend", response_class=HTMLResponse)
def trend(request: Request):
    return render(request, "trend", trend=lambda con, s: reporting.trend(con, s.as_of))


@app.get("/summary.md", response_class=PlainTextResponse)
def summary_md():
    from ..summary import build_summary
    with DB_LOCK:
        return build_summary(config.DB_PATH)


@app.get("/api/scoreboard")
def api_scoreboard():
    with DB_LOCK:
        return _api_scoreboard()


def _api_scoreboard():
    con = connect(config.DB_PATH)
    try:
        state = reporting.current_state(con)
        sb = reporting.scoreboard(con, state.as_of)
        return {"as_of": state.as_of.isoformat(), "rollups": sb["rollups"], "accounts": sb["accounts"]}
    finally:
        con.close()


@app.post("/actions/simulate-day")
def action_simulate_day():
    from ..datagen import simulate_next_day
    from ..pipeline import run_pipeline
    with DB_LOCK:
        simulate_next_day(config.DATA_DIR)
        run_pipeline(config.DATA_DIR, config.DB_PATH)
    return RedirectResponse("/", status_code=303)


@app.post("/actions/reset")
def action_reset():
    from ..datagen import generate_seed
    from ..pipeline import run_pipeline
    with DB_LOCK:
        generate_seed(config.DATA_DIR, config.load_state(config.DATA_DIR).month_start)
        run_pipeline(config.DATA_DIR, config.DB_PATH)
    return RedirectResponse("/", status_code=303)
