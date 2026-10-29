from datetime import date

from fastapi.testclient import TestClient

from continuous_close import config, reporting
from continuous_close.datagen import simulate_next_day
from continuous_close.db import connect
from continuous_close.pipeline import run_pipeline
from continuous_close.summary import build_summary


def test_pipeline_finds_the_planted_problems(workspace):
    _, db_path = workspace
    con = connect(db_path, read_only=True)
    try:
        state = reporting.current_state(con)
        assert state.as_of == date(2026, 10, 18)
        types = {m["type"] for m in reporting.mismatches(con, state.as_of)}
        assert {"timing_difference", "duplicate_payment", "amount_mismatch", "unrecorded_bank_fee"} <= types
        stats = reporting.recon_stats(con, state.as_of)
        assert stats["auto_clear_pct"] > 85
        accruals = reporting.accruals(con, state.as_of)
        assert {a["po_id"] for a in accruals} == {"PO-2402", "PO-2403", "PO-2404", "PO-2405"}
        assert round(sum(a["accrual_amount"] for a in accruals), 2) == 40_600.0
        trend = reporting.trend(con, state.as_of)
        assert [t["day"] for t in trend] == list(range(1, 19))
        sb = reporting.scoreboard(con, state.as_of)
        oi = sb["by_key"]["operating_income"]
        assert abs(oi["projected"] - (oi["actual"] + oi["accrual"] + oi["forecast"])) < 0.05
        assert oi["accrual"] == -40_600.0
    finally:
        con.close()


def test_morning_summary_markdown(workspace):
    _, db_path = workspace
    md = build_summary(db_path)
    assert md.startswith("# Continuous Close - morning summary for Sunday, October 18")
    assert "## Scoreboard" in md and "| **Operating Income** |" in md
    assert "Duplicate payment" in md and "PO-2402" in md and "## Today's asks" in md


def test_simulate_next_day_adds_activity_and_new_mismatches(workspace, tmp_path):
    import shutil
    data_dir, _ = workspace
    data_copy, db2 = tmp_path / "data", tmp_path / "cc2.duckdb"
    shutil.copytree(data_dir, data_copy)
    before = run_pipeline(data_copy, db2)
    result = simulate_next_day(data_copy, seed=7)
    assert result["as_of"] == date(2026, 10, 19)
    assert result["new_gl_lines"] > 0 and 1 <= len(result["planted"]) <= 2
    after = run_pipeline(data_copy, db2)
    assert after["as_of"] == date(2026, 10, 19) and after["gl_lines"] > before["gl_lines"]
    con = connect(db2, read_only=True)
    try:
        mm = reporting.mismatches(con, date(2026, 10, 19))
        # the Oct 15 check clears on Oct 19, so that timing difference is gone; new problems were planted
        assert not any(m["ref"] == "CHK-10443" for m in mm)
        assert len(mm) >= 3 + len(result["planted"])
    finally:
        con.close()


def test_dashboard_views_render(workspace, monkeypatch):
    data_dir, db_path = workspace
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    from continuous_close.web.app import app
    client = TestClient(app)
    for path, needle in [("/", "If the month ended today"), ("/mismatches", "Duplicate payment"),
                         ("/accruals", "Blue Ridge Print Co."), ("/trend", "Day-by-day projection"),
                         ("/summary.md", "morning summary")]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert needle in r.text, path
    api = client.get("/api/scoreboard").json()
    assert api["as_of"] == "2026-10-18" and len(api["accounts"]) == 16
