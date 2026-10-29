"""Bank reconciliation: match GL cash lines to bank statement lines and explain the leftovers."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from .accounts import BANK_FEES, CASH

FEE_RE = re.compile(r"\b(FEE|SERVICE CHARGE|ANALYSIS)\b", re.I)
TIMING_WINDOW_DAYS = 10
PAIR_WINDOW_DAYS = 5


def _book_cash_lines(gl_lines: list[dict]) -> list[dict]:
    out = []
    for l in gl_lines:
        if l["account"] != CASH:
            continue
        out.append({
            "entry_id": l["entry_id"], "date": l["date"], "amount": round(float(l["debit"] or 0) - float(l["credit"] or 0), 2),
            "counterparty": l.get("counterparty") or "", "ref": l.get("ref") or "", "description": l.get("description") or "",
        })
    return out


def _digits(x: float) -> str:
    return "".join(sorted(str(int(round(abs(x))))))


def reconcile(gl_lines: list[dict], bank_txns: list[dict], as_of: date) -> tuple[list[dict], list[dict]]:
    """Return (matches, mismatches). Inputs are dicts with python dates and floats."""
    books = _book_cash_lines(gl_lines)
    bank = [dict(b, amount=round(float(b["amount"]), 2)) for b in bank_txns]
    matches: list[dict] = []
    used_bank: set[str] = set()
    used_book: set[str] = set()

    def match(b: dict, t: dict, rule: str) -> None:
        matches.append({"book_entry_id": b["entry_id"], "bank_txn_id": t["bank_txn_id"], "amount": t["amount"],
                        "book_date": b["date"], "bank_date": t["date"], "counterparty": b["counterparty"],
                        "ref": b["ref"] or t["ref"], "rule": rule})
        used_book.add(b["entry_id"])
        used_bank.add(t["bank_txn_id"])

    # Pass 1: same reference + same amount
    by_ref: dict[str, list[dict]] = {}
    for t in bank:
        if t["ref"]:
            by_ref.setdefault(t["ref"], []).append(t)
    for b in books:
        for t in by_ref.get(b["ref"], []):
            if t["bank_txn_id"] not in used_bank and abs(t["amount"] - b["amount"]) < 0.005:
                match(b, t, "ref+amount")
                break

    # Pass 2: same counterparty + same amount within a few days
    for b in books:
        if b["entry_id"] in used_book:
            continue
        for t in bank:
            if t["bank_txn_id"] in used_bank or abs(t["amount"] - b["amount"]) >= 0.005:
                continue
            if t["counterparty"] == b["counterparty"] and abs((t["date"] - b["date"]).days) <= PAIR_WINDOW_DAYS:
                match(b, t, "counterparty+amount")
                break

    mismatches: list[dict] = []
    seq = 0

    def add(type_: str, severity: str, **kw) -> None:
        nonlocal seq
        seq += 1
        mismatches.append({"mismatch_id": f"MM-{as_of:%d}-{seq:02d}", "type": type_, "severity": severity, **kw})

    # Pass 3: same reference, different amount -> amount mismatch
    for b in books:
        if b["entry_id"] in used_book:
            continue
        for t in by_ref.get(b["ref"], []):
            if t["bank_txn_id"] in used_bank:
                continue
            diff = round(b["amount"] - t["amount"], 2)
            transposed = _digits(b["amount"]) == _digits(t["amount"])
            why = "the digits are the same in a different order - a transposition keying error" if transposed else "possibly a partial payment or keying error"
            add("amount_mismatch", "high" if abs(diff) >= 500 else "medium", txn_date=b["date"], counterparty=b["counterparty"],
                ref=b["ref"], book_entry_id=b["entry_id"], bank_txn_id=t["bank_txn_id"], book_amount=b["amount"],
                bank_amount=t["amount"], difference=diff,
                likely_reason=f"Books record {abs(b['amount']):,.2f} but the bank cleared {abs(t['amount']):,.2f} for {b['ref']} ({b['counterparty']}); {why}.",
                suggested_action=f"Confirm the vendor invoice amount, then correct {b['entry_id']} by {abs(diff):,.2f} so the books agree with the bank.")
            used_book.add(b["entry_id"])
            used_bank.add(t["bank_txn_id"])
            break

    # Leftover bank items
    matched_bank = [t for t in bank if t["bank_txn_id"] in used_bank]
    for t in bank:
        if t["bank_txn_id"] in used_bank:
            continue
        if FEE_RE.search(t["description"]) and abs(t["amount"]) < 250:
            add("unrecorded_bank_fee", "low", txn_date=t["date"], counterparty=t["counterparty"] or "Bank", ref=t["ref"],
                book_entry_id="", bank_txn_id=t["bank_txn_id"], book_amount=0.0, bank_amount=t["amount"], difference=-t["amount"],
                likely_reason=f"The bank charged a {t['description'].lower()} of {abs(t['amount']):,.2f} on {t['date']:%b %d}; nothing is booked in the GL.",
                suggested_action=f"Post Dr {BANK_FEES} Bank Fees / Cr {CASH} Cash for {abs(t['amount']):,.2f}.")
            continue
        twin = next((m for m in matched_bank if m["counterparty"] == t["counterparty"]
                     and abs(m["amount"] - t["amount"]) < 0.005 and abs((m["date"] - t["date"]).days) <= 7), None)
        if twin:
            add("duplicate_payment", "high", txn_date=t["date"], counterparty=t["counterparty"], ref=t["ref"] or twin["ref"],
                book_entry_id="", bank_txn_id=t["bank_txn_id"], book_amount=0.0, bank_amount=t["amount"], difference=-t["amount"],
                likely_reason=f"The bank debited {t['counterparty']} {abs(t['amount']):,.2f} twice ({twin['date']:%b %d} and {t['date']:%b %d}) under {t['ref'] or twin['ref']}; only one payment is booked - this looks like a duplicate payment.",
                suggested_action=f"Ask {t['counterparty']} for a refund or credit; book Dr Vendor Receivable / Cr {CASH} Cash for {abs(t['amount']):,.2f} until recovered.")
            continue
        add("unknown_bank_item", "medium", txn_date=t["date"], counterparty=t["counterparty"], ref=t["ref"], book_entry_id="",
            bank_txn_id=t["bank_txn_id"], book_amount=0.0, bank_amount=t["amount"], difference=-t["amount"],
            likely_reason=f"Bank shows '{t['description']}' for {t['amount']:,.2f} with no corresponding GL entry.",
            suggested_action="Investigate with treasury and book the entry once identified.")

    # Leftover book items
    for b in books:
        if b["entry_id"] in used_book:
            continue
        age = (as_of - b["date"]).days
        if age <= TIMING_WINDOW_DAYS:
            kind = "check" if b["ref"].startswith("CHK") else "ACH"
            add("timing_difference", "low" if age <= 5 else "medium", txn_date=b["date"], counterparty=b["counterparty"], ref=b["ref"],
                book_entry_id=b["entry_id"], bank_txn_id="", book_amount=b["amount"], bank_amount=0.0, difference=b["amount"],
                likely_reason=f"{'Payment' if b['amount'] < 0 else 'Receipt'} {b['ref']} ({b['counterparty']}, {abs(b['amount']):,.2f}) was booked on {b['date']:%b %d} but has not cleared the bank after {age} day{'s' if age != 1 else ''} - most likely {kind} float / timing.",
                suggested_action="No entry needed. Keep it on the outstanding list; escalate if it has not cleared within 10 days.")
        else:
            add("stale_outstanding", "medium", txn_date=b["date"], counterparty=b["counterparty"], ref=b["ref"],
                book_entry_id=b["entry_id"], bank_txn_id="", book_amount=b["amount"], bank_amount=0.0, difference=b["amount"],
                likely_reason=f"{b['ref']} ({b['counterparty']}, {abs(b['amount']):,.2f}) has been outstanding for {age} days.",
                suggested_action="Confirm with the counterparty whether the payment was received; consider voiding and reissuing.")

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    mismatches.sort(key=lambda m: (severity_rank[m["severity"]], -abs(m["difference"])))
    return matches, mismatches


def mismatch_type_label(t: str) -> str:
    return {
        "timing_difference": "Timing difference", "duplicate_payment": "Duplicate payment",
        "amount_mismatch": "Amount mismatch", "unrecorded_bank_fee": "Unrecorded bank fee",
        "unknown_bank_item": "Unknown bank item", "stale_outstanding": "Stale outstanding item",
    }.get(t, t)


def summarize_types(mismatches: list[dict]) -> Counter:
    return Counter(m["type"] for m in mismatches)
