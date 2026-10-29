"""Morning summary in Markdown."""
from __future__ import annotations

from pathlib import Path

from . import reporting
from .config import COMPANY, DB_PATH, SEGMENT
from .db import connect


def fmt(x: float, signed: bool = False) -> str:
    s = f"{abs(x):,.0f}"
    if signed:
        return ("+" if x >= 0 else "-") + "$" + s
    return ("-" if x < 0 else "") + "$" + s


def fav(x: float) -> str:
    return "favorable" if x >= 0 else "unfavorable"


def build_summary(db_path: Path = DB_PATH) -> str:
    con = connect(db_path, read_only=True)
    try:
        state = reporting.current_state(con)
        sb = reporting.scoreboard(con, state.as_of)
        k = sb["by_key"]
        mms = reporting.mismatches(con, state.as_of)
        accs = reporting.accruals(con, state.as_of)
        stats = reporting.recon_stats(con, state.as_of)
        oi, rev, opex, gm = k["operating_income"], k["revenue"], k["opex"], k["gross_margin"]

        lines = [f"# Continuous Close - morning summary for {state.as_of:%A, %B %-d}", "",
                 f"**{COMPANY} / {SEGMENT}** - day {state.day} of {state.days_in_month} ({round(state.pct_elapsed * 100)}% of the month elapsed)", ""]
        headline = (f"If the month ended today we would be at {fmt(oi['actual'] + oi['accrual'])} operating income. "
                    f"We are tracking to finish at **{fmt(oi['projected'])}** vs a budget of {fmt(oi['budget'])} "
                    f"({fmt(oi['variance'], True)}, {oi['variance_pct']:+.1f}% {fav(oi['variance'])}).")
        if "delta" in oi:
            headline += f" That is {fmt(oi['delta'], True)} versus yesterday's projection."
        lines += [headline, "", "## Scoreboard (projected close vs budget)", "",
                  "| Line | MTD actual | Accruals | Forecast (rest of month) | Projected close | Budget | Variance |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for r in sb["rollups"]:
            label = f"**{r['label']}**" if r["is_subtotal"] else r["label"]
            lines.append(f"| {label} | {fmt(r['actual'])} | {fmt(r['accrual'])} | {fmt(r['forecast'])} | {fmt(r['projected'])} | {fmt(r['budget'])} | {fmt(r['variance'], True)} ({r['variance_pct']:+.1f}%) |")
        lines.append("")

        drivers = sorted([a for a in sb["accounts"]], key=lambda a: a["variance"])
        worst = [a for a in drivers if a["variance"] < -1000][:3]
        best = [a for a in reversed(drivers) if a["variance"] > 1000][:3]
        if worst or best:
            lines += ["## What is driving the variance", ""]
            for a in worst:
                lines.append(f"- {a['name']}: projected {fmt(a['projected'])} vs budget {fmt(a['budget'])} ({fmt(a['variance'], True)} unfavorable)")
            for a in best:
                lines.append(f"- {a['name']}: projected {fmt(a['projected'])} vs budget {fmt(a['budget'])} ({fmt(a['variance'], True)} favorable)")
            lines.append("")

        lines += ["## Bank reconciliation", "",
                  f"- {stats['cleared']} of {stats['cleared'] + stats['open']} cash items auto-cleared ({stats['auto_clear_pct']}%), {fmt(stats['cleared_amount'])} matched.",
                  f"- {stats['open']} open mismatch{'es' if stats['open'] != 1 else ''} totalling {fmt(stats['open_amount'])}:" if stats["open"] else "- No open mismatches.", ""]
        for m in mms:
            lines.append(f"  - **{m['type_label']}** ({m['severity']}) - {m['counterparty']}, {fmt(abs(m['difference']))}: {m['likely_reason']} _Action: {m['suggested_action']}_")
        lines.append("")

        total_acc = sum(a["accrual_amount"] for a in accs)
        lines += ["## Accruals (goods received, not invoiced)", "",
                  f"- {len(accs)} item{'s' if len(accs) != 1 else ''}, {fmt(total_acc)} accrued to Accrued Liabilities (2100) and included in the projection above." if accs else "- Nothing received without an invoice.", ""]
        for a in accs:
            lines.append(f"  - {a['po_id']} {a['vendor']} - {a['description']}: {fmt(a['accrual_amount'])} to {a['account_name']} (received {a['received_date']:%b %-d}, waiting {a['days_waiting']} days)")
        lines += ["", "## Today's asks", ""]
        asks = [f"- {m['suggested_action']}" for m in mms if m["severity"] == "high"]
        if not asks:
            asks = ["- Nothing urgent. Keep an eye on the outstanding items above."]
        lines += asks
        return "\n".join(lines) + "\n"
    finally:
        con.close()
