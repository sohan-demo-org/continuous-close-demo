"""Projected close = MTD actuals + accruals + forecast for the rest of the month, vs budget."""
from __future__ import annotations

from datetime import date

from .accounts import CHART
from .config import days_in_month

ROLLUPS = [
    ("revenue", "Revenue", ["revenue"]),
    ("cogs", "Cost of Sales", ["cogs"]),
    ("gross_margin", "Gross Margin", None),
    ("opex", "Operating Expenses", ["opex"]),
    ("operating_income", "Operating Income", None),
]


def project(gl_lines: list[dict], accruals: list[dict], budgets: dict[str, float], as_of: date) -> list[dict]:
    day = as_of.day
    dim = days_in_month(as_of)
    remaining = dim - day
    actual: dict[str, float] = {}
    for l in gl_lines:
        if l["date"] <= as_of:
            actual[l["account"]] = actual.get(l["account"], 0.0) + float(l["debit"] or 0) - float(l["credit"] or 0)
    accrued: dict[str, float] = {}
    for a in accruals:
        accrued[a["account"]] = accrued.get(a["account"], 0.0) + a["accrual_amount"]

    out = []
    for acct in CHART:
        if not acct.is_pnl:
            continue
        dr_cr = actual.get(acct.number, 0.0)
        act = -dr_cr if acct.type == "revenue" else dr_cr
        acc = accrued.get(acct.number, 0.0)
        mtd = act + acc
        budget = budgets.get(acct.number, acct.budget)
        if acct.forecast_method == "run_rate":
            forecast = mtd / day * remaining
        elif acct.forecast_method == "budget_fill":
            forecast = max(0.0, budget - mtd) if budget >= 0 else 0.0
        else:
            forecast = 0.0
        projected = mtd + forecast
        variance = (projected - budget) if acct.type == "revenue" else (budget - projected)  # favorable > 0
        out.append({
            "account": acct.number, "name": acct.name, "type": acct.type, "method": acct.forecast_method,
            "actual": round(act, 2), "accrual": round(acc, 2), "forecast": round(forecast, 2),
            "projected": round(projected, 2), "budget": round(budget, 2), "variance": round(variance, 2),
            "variance_pct": round(variance / abs(budget) * 100, 1) if budget else 0.0,
        })
    return out


def rollups(projection_rows: list[dict]) -> list[dict]:
    def total(types: list[str], key: str) -> float:
        return sum(r[key] for r in projection_rows if r["type"] in types)

    keys = ["actual", "accrual", "forecast", "projected", "budget"]
    rev = {k: total(["revenue"], k) for k in keys}
    cogs = {k: total(["cogs"], k) for k in keys}
    opex = {k: total(["opex"], k) for k in keys}
    gm = {k: rev[k] - cogs[k] for k in keys}
    oi = {k: gm[k] - opex[k] for k in keys}
    out = []
    for key, label, vals, sign in (("revenue", "Revenue", rev, 1), ("cogs", "Cost of Sales", cogs, -1),
                                   ("gross_margin", "Gross Margin", gm, 1), ("opex", "Operating Expenses", opex, -1),
                                   ("operating_income", "Operating Income", oi, 1)):
        variance = (vals["projected"] - vals["budget"]) * sign
        row = {"key": key, "label": label, **{k: round(v, 2) for k, v in vals.items()},
               "variance": round(variance, 2),
               "variance_pct": round(variance / abs(vals["budget"]) * 100, 1) if vals["budget"] else 0.0,
               "is_subtotal": key in ("gross_margin", "operating_income")}
        out.append(row)
    return out
