"""DuckDB storage: source tables loaded from CSV plus pipeline output tables."""
from __future__ import annotations

from pathlib import Path

import duckdb

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    as_of DATE, run_at TIMESTAMP, gl_lines INTEGER, bank_txns INTEGER,
    matched INTEGER, mismatches INTEGER, accruals INTEGER);
CREATE TABLE IF NOT EXISTS recon_matches (
    as_of DATE, book_entry_id VARCHAR, bank_txn_id VARCHAR, amount DOUBLE,
    book_date DATE, bank_date DATE, counterparty VARCHAR, ref VARCHAR, rule VARCHAR);
CREATE TABLE IF NOT EXISTS mismatches (
    as_of DATE, mismatch_id VARCHAR, type VARCHAR, severity VARCHAR, txn_date DATE,
    counterparty VARCHAR, ref VARCHAR, book_entry_id VARCHAR, bank_txn_id VARCHAR,
    book_amount DOUBLE, bank_amount DOUBLE, difference DOUBLE,
    likely_reason VARCHAR, suggested_action VARCHAR);
CREATE TABLE IF NOT EXISTS accruals (
    as_of DATE, po_id VARCHAR, vendor VARCHAR, account VARCHAR, account_name VARCHAR,
    description VARCHAR, po_amount DOUBLE, received_amount DOUBLE, invoiced_amount DOUBLE,
    accrual_amount DOUBLE, received_date DATE, days_waiting INTEGER, rule VARCHAR);
CREATE TABLE IF NOT EXISTS projection (
    as_of DATE, account VARCHAR, name VARCHAR, type VARCHAR, method VARCHAR,
    actual DOUBLE, accrual DOUBLE, forecast DOUBLE, projected DOUBLE, budget DOUBLE,
    variance DOUBLE, variance_pct DOUBLE);
"""

SOURCE_TABLES = {
    "accounts": ("chart_of_accounts.csv", {"number": "VARCHAR", "name": "VARCHAR", "type": "VARCHAR", "forecast_method": "VARCHAR"}),
    "budget": ("budget.csv", {"account": "VARCHAR", "month": "VARCHAR", "amount": "DOUBLE"}),
    "gl_lines": ("general_ledger.csv", {"entry_id": "VARCHAR", "line_no": "INTEGER", "date": "DATE", "account": "VARCHAR",
                                        "debit": "DOUBLE", "credit": "DOUBLE", "description": "VARCHAR", "counterparty": "VARCHAR",
                                        "ref": "VARCHAR", "source": "VARCHAR", "po_id": "VARCHAR"}),
    "bank_txns": ("bank_statement.csv", {"bank_txn_id": "VARCHAR", "date": "DATE", "description": "VARCHAR", "amount": "DOUBLE",
                                         "ref": "VARCHAR", "counterparty": "VARCHAR"}),
    "purchase_orders": ("purchase_orders.csv", {"po_id": "VARCHAR", "vendor": "VARCHAR", "account": "VARCHAR", "amount": "DOUBLE",
                                                "order_date": "DATE", "description": "VARCHAR"}),
    "receipts": ("receipts.csv", {"receipt_id": "VARCHAR", "po_id": "VARCHAR", "received_date": "DATE", "amount": "DOUBLE", "note": "VARCHAR"}),
}


def connect(path: Path | str = DB_PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def load_sources(con: duckdb.DuckDBPyConnection, data_dir: Path) -> dict[str, int]:
    counts = {}
    for table, (filename, cols) in SOURCE_TABLES.items():
        path = data_dir / filename
        col_spec = ", ".join(f"'{c}': '{t}'" for c, t in cols.items())
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv('{path}', header=true, columns={{{col_spec}}})")
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | tuple = ()) -> list[dict]:
    cur = con.execute(sql, list(params))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con: duckdb.DuckDBPyConnection, sql: str, params: list | tuple = ()) -> dict | None:
    r = rows(con, sql, params)
    return r[0] if r else None
