"""Accrual rule: goods (or services) received but not yet invoiced (GRNI)."""
from __future__ import annotations

from datetime import date

from .accounts import BY_NUMBER

RULE = "GRNI: received on PO, no vendor invoice posted"


def grni_accruals(purchase_orders: list[dict], receipts: list[dict], gl_lines: list[dict], as_of: date) -> list[dict]:
    received: dict[str, float] = {}
    first_receipt: dict[str, date] = {}
    for r in receipts:
        if r["received_date"] <= as_of:
            received[r["po_id"]] = received.get(r["po_id"], 0.0) + float(r["amount"])
            first_receipt[r["po_id"]] = min(first_receipt.get(r["po_id"], r["received_date"]), r["received_date"])
    invoiced: dict[str, float] = {}
    for l in gl_lines:
        if l.get("po_id") and l["date"] <= as_of and l["account"] != "2000":
            invoiced[l["po_id"]] = invoiced.get(l["po_id"], 0.0) + float(l["debit"] or 0) - float(l["credit"] or 0)
    out = []
    for po in purchase_orders:
        rec = received.get(po["po_id"], 0.0)
        inv = invoiced.get(po["po_id"], 0.0)
        accrual = round(rec - inv, 2)
        if accrual <= 0.005:
            continue
        acct = BY_NUMBER.get(po["account"])
        out.append({
            "po_id": po["po_id"], "vendor": po["vendor"], "account": po["account"],
            "account_name": acct.name if acct else po["account"], "description": po["description"],
            "po_amount": float(po["amount"]), "received_amount": round(rec, 2), "invoiced_amount": round(inv, 2),
            "accrual_amount": accrual, "received_date": first_receipt[po["po_id"]],
            "days_waiting": (as_of - first_receipt[po["po_id"]]).days, "rule": RULE,
        })
    out.sort(key=lambda a: -a["accrual_amount"])
    return out
