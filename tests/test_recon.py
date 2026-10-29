from datetime import date

from continuous_close.recon import reconcile

AS_OF = date(2026, 10, 18)


def gl(entry, d, amount, cp, ref):
    """A cash line: positive = money in (debit cash), negative = money out."""
    return {"entry_id": entry, "date": d, "account": "1000", "debit": max(amount, 0), "credit": max(-amount, 0),
            "counterparty": cp, "ref": ref, "description": "", "po_id": ""}


def bank(bid, d, amount, cp, ref, desc="ACH"):
    return {"bank_txn_id": bid, "date": d, "amount": amount, "counterparty": cp, "ref": ref, "description": desc}


def test_exact_matches_auto_clear():
    matches, mismatches = reconcile(
        [gl("JE-1", date(2026, 10, 3), -500, "Acme", "PAY-1"), gl("JE-2", date(2026, 10, 4), 1200, "Cust", "RCPT-1")],
        [bank("BT-1", date(2026, 10, 3), -500, "Acme", "PAY-1"), bank("BT-2", date(2026, 10, 4), 1200, "Cust", "RCPT-1")], AS_OF)
    assert len(matches) == 2 and mismatches == []
    assert {m["rule"] for m in matches} == {"ref+amount"}


def test_counterparty_amount_fallback_when_bank_has_no_ref():
    matches, mismatches = reconcile([gl("JE-1", date(2026, 10, 3), -500, "Acme", "CHK-9")],
                                    [bank("BT-1", date(2026, 10, 5), -500, "Acme", "", "CHECK #9")], AS_OF)
    assert len(matches) == 1 and matches[0]["rule"] == "counterparty+amount" and not mismatches


def test_timing_difference_for_recent_uncleared_check():
    _, mm = reconcile([gl("JE-1", date(2026, 10, 15), -12480, "Keystone", "CHK-10443")], [], AS_OF)
    assert [m["type"] for m in mm] == ["timing_difference"]
    assert "not cleared" in mm[0]["likely_reason"] and mm[0]["severity"] == "low"


def test_stale_outstanding_after_ten_days():
    _, mm = reconcile([gl("JE-1", date(2026, 10, 2), -900, "Old Vendor", "CHK-1")], [], AS_OF)
    assert mm[0]["type"] == "stale_outstanding"


def test_duplicate_payment_detected():
    books = [gl("JE-1", date(2026, 10, 11), -7940, "Harborline", "PAY-5")]
    bk = [bank("BT-1", date(2026, 10, 11), -7940, "Harborline", "PAY-5"), bank("BT-2", date(2026, 10, 12), -7940, "Harborline", "PAY-5")]
    matches, mm = reconcile(books, bk, AS_OF)
    assert len(matches) == 1
    assert mm[0]["type"] == "duplicate_payment" and mm[0]["severity"] == "high" and mm[0]["bank_txn_id"] == "BT-2"
    assert "twice" in mm[0]["likely_reason"]


def test_amount_mismatch_flags_transposition():
    _, mm = reconcile([gl("JE-1", date(2026, 10, 13), -8750, "Summit", "PAY-9")],
                      [bank("BT-1", date(2026, 10, 13), -7850, "Summit", "PAY-9")], AS_OF)
    assert mm[0]["type"] == "amount_mismatch" and mm[0]["difference"] == -900.0
    assert "transposition" in mm[0]["likely_reason"] and "900.00" in mm[0]["suggested_action"]


def test_unrecorded_bank_fee():
    _, mm = reconcile([], [bank("BT-1", date(2026, 10, 12), -35, "First Harbor Bank", "", "WIRE TRANSFER FEE")], AS_OF)
    assert mm[0]["type"] == "unrecorded_bank_fee" and "6900" in mm[0]["suggested_action"]


def test_unknown_bank_item_when_nothing_explains_it():
    _, mm = reconcile([], [bank("BT-1", date(2026, 10, 12), -4000, "Mystery LLC", "", "ACH DEBIT MYSTERY LLC")], AS_OF)
    assert mm[0]["type"] == "unknown_bank_item"
