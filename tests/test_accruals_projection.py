from datetime import date

from continuous_close.accruals import grni_accruals
from continuous_close.projection import project, rollups

AS_OF = date(2026, 10, 18)


def test_grni_accrues_received_not_invoiced_only():
    pos = [{"po_id": "PO-1", "vendor": "V", "account": "6400", "amount": 20000, "description": "ads"},
           {"po_id": "PO-2", "vendor": "W", "account": "5100", "amount": 5000, "description": "freight"},
           {"po_id": "PO-3", "vendor": "X", "account": "6950", "amount": 800, "description": "later"}]
    receipts = [{"po_id": "PO-1", "received_date": date(2026, 10, 10), "amount": 12000},
                {"po_id": "PO-2", "received_date": date(2026, 10, 5), "amount": 5000},
                {"po_id": "PO-3", "received_date": date(2026, 10, 25), "amount": 800}]  # future
    gl = [{"po_id": "PO-2", "date": date(2026, 10, 8), "account": "5100", "debit": 5000, "credit": 0},
          {"po_id": "PO-2", "date": date(2026, 10, 8), "account": "2000", "debit": 0, "credit": 5000}]
    acc = grni_accruals(pos, receipts, gl, AS_OF)
    assert [a["po_id"] for a in acc] == ["PO-1"]
    assert acc[0]["accrual_amount"] == 12000 and acc[0]["days_waiting"] == 8 and acc[0]["account_name"] == "Marketing & Advertising"


def test_projection_run_rate_and_budget_fill():
    # 18 of 31 days elapsed. Revenue 4000 booked 900k so far -> run-rate projects 900k/18*31 = 1.55M
    gl = [{"date": date(2026, 10, 10), "account": "4000", "debit": 0, "credit": 900_000},
          {"date": date(2026, 10, 10), "account": "1200", "debit": 900_000, "credit": 0},
          {"date": date(2026, 10, 1), "account": "6200", "debit": 28_500, "credit": 0},   # rent, fully booked
          {"date": date(2026, 10, 15), "account": "6000", "debit": 92_500, "credit": 0}]  # first payroll
    accruals = [{"account": "5100", "accrual_amount": 9_860}]
    budgets = {"4000": 1_450_000, "6200": 28_500, "6000": 185_000, "5100": 62_000}
    rows = {r["account"]: r for r in project(gl, accruals, budgets, AS_OF)}
    assert rows["4000"]["actual"] == 900_000 and rows["4000"]["projected"] == 1_550_000 and rows["4000"]["variance"] == 100_000
    assert rows["6200"]["forecast"] == 0 and rows["6200"]["projected"] == 28_500 and rows["6200"]["variance"] == 0
    assert rows["6000"]["forecast"] == 92_500 and rows["6000"]["projected"] == 185_000  # fills to budget
    assert rows["5100"]["accrual"] == 9_860 and rows["5100"]["projected"] == round(9_860 / 18 * 31, 2)
    assert rows["6700"]["projected"] == 15_000  # nothing booked yet, fixed cost fills to budget

    roll = {r["key"]: r for r in rollups(list(rows.values()))}
    assert roll["gross_margin"]["projected"] == round(roll["revenue"]["projected"] - roll["cogs"]["projected"], 2)
    assert roll["operating_income"]["projected"] == round(roll["gross_margin"]["projected"] - roll["opex"]["projected"], 2)
