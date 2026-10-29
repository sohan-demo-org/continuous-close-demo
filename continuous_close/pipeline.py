"""The daily pipeline: load sources into DuckDB, reconcile, accrue, project, snapshot."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from .accruals import grni_accruals
from .config import DATA_DIR, DB_PATH, State, load_state
from .db import connect, load_sources, rows
from .projection import project
from .recon import reconcile


def _insert(con, table: str, cols: list[str], records: list[dict], as_of: date) -> None:
    if not records:
        return
    placeholders = ", ".join("?" for _ in range(len(cols) + 1))
    con.executemany(f"INSERT INTO {table} (as_of, {', '.join(cols)}) VALUES ({placeholders})",
                    [[as_of] + [r.get(c) for c in cols] for r in records])


MATCH_COLS = ["book_entry_id", "bank_txn_id", "amount", "book_date", "bank_date", "counterparty", "ref", "rule"]
MISMATCH_COLS = ["mismatch_id", "type", "severity", "txn_date", "counterparty", "ref", "book_entry_id", "bank_txn_id",
                 "book_amount", "bank_amount", "difference", "likely_reason", "suggested_action"]
ACCRUAL_COLS = ["po_id", "vendor", "account", "account_name", "description", "po_amount", "received_amount",
                "invoiced_amount", "accrual_amount", "received_date", "days_waiting", "rule"]
PROJ_COLS = ["account", "name", "type", "method", "actual", "accrual", "forecast", "projected", "budget", "variance", "variance_pct"]


def run_pipeline(data_dir: Path = DATA_DIR, db_path: Path = DB_PATH, backfill: bool = True) -> dict:
    """Rebuild the database from the source files as of the current state.

    With ``backfill`` the projection is recomputed for every day of the month so far, exactly as it
    would have looked that morning (only data dated on or before that day), which feeds the Trend view.
    """
    state: State = load_state(data_dir)
    con = connect(db_path)
    try:
        counts = load_sources(con, data_dir)
        gl = rows(con, "SELECT * FROM gl_lines WHERE date <= ? ORDER BY date, entry_id, line_no", [state.as_of])
        bank = rows(con, "SELECT * FROM bank_txns WHERE date <= ? ORDER BY date, bank_txn_id", [state.as_of])
        pos = rows(con, "SELECT * FROM purchase_orders ORDER BY po_id")
        receipts = rows(con, "SELECT * FROM receipts ORDER BY received_date")
        budgets = {r["account"]: r["amount"] for r in rows(con, "SELECT account, amount FROM budget WHERE month = ?",
                                                             [state.month_start.strftime("%Y-%m")])}

        days = range(1, state.day + 1) if backfill else [state.day]
        for table in ("recon_matches", "mismatches", "accruals", "projection", "pipeline_runs"):
            if backfill:
                con.execute(f"DELETE FROM {table}")
            else:
                con.execute(f"DELETE FROM {table} WHERE as_of = ?", [state.as_of])
        result = {}
        for day in days:
            as_of = state.month_start + timedelta(days=day - 1)
            gl_d = [l for l in gl if l["date"] <= as_of]
            bank_d = [b for b in bank if b["date"] <= as_of]
            matches, mismatches = reconcile(gl_d, bank_d, as_of)
            accruals = grni_accruals(pos, receipts, gl_d, as_of)
            projection = project(gl_d, accruals, budgets, as_of)
            _insert(con, "recon_matches", MATCH_COLS, matches, as_of)
            _insert(con, "mismatches", MISMATCH_COLS, mismatches, as_of)
            _insert(con, "accruals", ACCRUAL_COLS, accruals, as_of)
            _insert(con, "projection", PROJ_COLS, projection, as_of)
            con.execute("INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [as_of, datetime.now(), len(gl_d), len(bank_d), len(matches), len(mismatches), len(accruals)])
            result = {"as_of": as_of, "gl_lines": len(gl_d), "bank_txns": len(bank_d), "matched": len(matches),
                      "mismatches": len(mismatches), "accruals": len(accruals), "sources": counts}
        con.execute("INSERT OR REPLACE INTO meta VALUES ('as_of', ?)", [state.as_of.isoformat()])
        con.execute("INSERT OR REPLACE INTO meta VALUES ('month_start', ?)", [state.month_start.isoformat()])
        con.execute("INSERT OR REPLACE INTO meta VALUES ('run_at', ?)", [datetime.now().isoformat(timespec='seconds')])
        return result
    finally:
        con.close()
