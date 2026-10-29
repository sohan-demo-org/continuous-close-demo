"""Read-side queries shared by the dashboard and the morning summary."""
from __future__ import annotations

from datetime import date, timedelta

from .config import COMPANY, SEGMENT, State
from .db import one, rows
from .projection import rollups
from .recon import mismatch_type_label


def current_state(con) -> State:
    meta = {r["key"]: r["value"] for r in rows(con, "SELECT key, value FROM meta")}
    if "as_of" not in meta:
        raise RuntimeError("Pipeline has not run yet. Run `cc pipeline` first.")
    return State(date.fromisoformat(meta["month_start"]), date.fromisoformat(meta["as_of"]))


def projection_rows(con, as_of: date) -> list[dict]:
    return rows(con, "SELECT * FROM projection WHERE as_of = ? ORDER BY account", [as_of])


def scoreboard(con, as_of: date) -> dict:
    proj = projection_rows(con, as_of)
    roll = rollups(proj)
    prev = rollups(projection_rows(con, as_of - timedelta(days=1))) if as_of.day > 1 else None
    by_key = {r["key"]: r for r in roll}
    if prev:
        for p in prev:
            by_key[p["key"]]["prev_projected"] = p["projected"]
            by_key[p["key"]]["delta"] = round(by_key[p["key"]]["projected"] - p["projected"], 2)
    return {"rollups": roll, "accounts": proj, "by_key": by_key}


def mismatches(con, as_of: date) -> list[dict]:
    out = rows(con, """SELECT * FROM mismatches WHERE as_of = ?
                       ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, abs(difference) DESC""", [as_of])
    for m in out:
        m["type_label"] = mismatch_type_label(m["type"])
    return out


def recon_stats(con, as_of: date) -> dict:
    r = one(con, "SELECT matched, mismatches, bank_txns, gl_lines FROM pipeline_runs WHERE as_of = ? ORDER BY run_at DESC LIMIT 1", [as_of]) or {}
    cleared = one(con, "SELECT count(*) n, coalesce(sum(abs(amount)),0) amt FROM recon_matches WHERE as_of = ?", [as_of])
    open_ = one(con, "SELECT count(*) n, coalesce(sum(abs(difference)),0) amt FROM mismatches WHERE as_of = ?", [as_of])
    total = cleared["n"] + open_["n"]
    return {"cleared": cleared["n"], "cleared_amount": cleared["amt"], "open": open_["n"], "open_amount": open_["amt"],
            "auto_clear_pct": round(cleared["n"] / total * 100, 1) if total else 100.0, **r}


def accruals(con, as_of: date) -> list[dict]:
    return rows(con, "SELECT * FROM accruals WHERE as_of = ? ORDER BY accrual_amount DESC", [as_of])


def open_pos(con, as_of: date) -> list[dict]:
    return rows(con, """
        SELECT p.po_id, p.vendor, p.account, p.description, p.amount, p.order_date,
               coalesce((SELECT sum(amount) FROM receipts r WHERE r.po_id = p.po_id AND r.received_date <= ?), 0) AS received,
               coalesce((SELECT sum(debit - credit) FROM gl_lines g WHERE g.po_id = p.po_id AND g.account <> '2000' AND g.date <= ?), 0) AS invoiced
        FROM purchase_orders p WHERE p.order_date <= ? ORDER BY p.po_id""", [as_of, as_of, as_of])


def trend(con, as_of: date) -> list[dict]:
    days = rows(con, "SELECT DISTINCT as_of FROM projection WHERE as_of <= ? ORDER BY as_of", [as_of])
    out = []
    for d in days:
        roll = {r["key"]: r for r in rollups(projection_rows(con, d["as_of"]))}
        mm = one(con, "SELECT count(*) n, coalesce(sum(abs(difference)),0) amt FROM mismatches WHERE as_of = ?", [d["as_of"]])
        acc = one(con, "SELECT coalesce(sum(accrual_amount),0) amt FROM accruals WHERE as_of = ?", [d["as_of"]])
        out.append({
            "as_of": d["as_of"], "day": d["as_of"].day,
            "revenue_projected": roll["revenue"]["projected"], "revenue_actual": roll["revenue"]["actual"], "revenue_budget": roll["revenue"]["budget"],
            "oi_projected": roll["operating_income"]["projected"], "oi_actual": roll["operating_income"]["actual"] + roll["operating_income"]["accrual"],
            "oi_budget": roll["operating_income"]["budget"],
            "opex_projected": roll["opex"]["projected"], "opex_budget": roll["opex"]["budget"],
            "gm_projected": roll["gross_margin"]["projected"], "gm_budget": roll["gross_margin"]["budget"],
            "mismatches": mm["n"], "mismatch_amount": mm["amt"], "accruals": acc["amt"],
        })
    return out


def header(con, state: State) -> dict:
    run_at = one(con, "SELECT value FROM meta WHERE key = 'run_at'")
    return {"company": COMPANY, "segment": SEGMENT, "state": state, "as_of": state.as_of,
            "day": state.day, "days_in_month": state.days_in_month, "pct_elapsed": round(state.pct_elapsed * 100),
            "days_left": state.days_in_month - state.day, "run_at": run_at["value"] if run_at else None}
