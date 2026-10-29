"""Synthetic source data: general ledger, bank statement, POs/receipts and budget.

The data is deterministic (seeded RNG) and includes a handful of planted
reconciliation problems so there is something for the pipeline to find.
"""
from __future__ import annotations

import csv
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .accounts import ACCRUED, AP, AR, BANK_FEES, CASH, CHART, INVENTORY
from .config import DATA_DIR, SEED_AS_OF_DAY, State, current_month, save_state

CUSTOMERS = [
    "Harbor Fresh Markets", "Pinecrest Grocers", "Metro Deli Group", "Blue Anchor Cafés",
    "Northside Hospitality", "Granite State Foods", "Riverbend Co-op", "Summit Ski Resorts",
    "Lantern Hotels", "Coastal Convenience",
]

# vendor -> (expense account, bank description style)
VENDORS = {
    "Harborline Logistics": "5100",
    "Northgate Freight": "5100",
    "Keystone Packaging": "5000",
    "Summit Glassworks": "1300",
    "Cascade Cane Sugar": "1300",
    "Blue Ridge Print Co.": "6400",
    "Lakeside Media Group": "6400",
    "Clearwater Office Supply": "6950",
    "Meridian IT Services": "6500",
    "Atlas Travel Desk": "6600",
    "Evergreen Advisory LLP": "6700",
}

AP_BILL_RANGES = {  # account -> (min, max) for ad-hoc vendor bills
    "5100": (1_600, 4_800),
    "1300": (9_000, 26_000),
    "6400": (400, 1_900),
    "6950": (90, 420),
    "6600": (300, 2_200),
}

GL_FIELDS = ["entry_id", "line_no", "date", "account", "debit", "credit", "description",
             "counterparty", "ref", "source", "po_id"]
BANK_FIELDS = ["bank_txn_id", "date", "description", "amount", "ref", "counterparty"]
PO_FIELDS = ["po_id", "vendor", "account", "amount", "order_date", "description"]
RECEIPT_FIELDS = ["receipt_id", "po_id", "received_date", "amount", "note"]
BUDGET_FIELDS = ["account", "month", "amount"]
ACCOUNT_FIELDS = ["number", "name", "type", "forecast_method"]


def money(x: float) -> float:
    return round(x + 1e-9, 2)


@dataclass
class Ledger:
    gl: list[dict] = field(default_factory=list)
    bank: list[dict] = field(default_factory=list)
    pos: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: {
        "JE": 1000, "BT": 5000, "RCPT": 7000, "PAY": 1000, "CHK": 10440, "INV": 30100,
        "BILL": 8800, "PO": 2400, "RC": 600,
    })

    # ------------------------------------------------------------------ ids
    def next_id(self, kind: str, width: int = 4) -> str:
        self.counters[kind] += 1
        return f"{kind}-{self.counters[kind]:0{width}d}"

    # ------------------------------------------------------------- posting
    def post(self, d: date, lines: list[tuple[str, float, float]], description: str,
             counterparty: str = "", ref: str = "", source: str = "GL", po_id: str = "") -> str:
        """Post a balanced journal entry. lines = [(account, debit, credit), ...]."""
        entry_id = self.next_id("JE")
        total_dr = sum(l[1] for l in lines)
        total_cr = sum(l[2] for l in lines)
        if abs(total_dr - total_cr) > 0.005:
            raise ValueError(f"Unbalanced entry {entry_id}: {total_dr} != {total_cr}")
        for i, (acct, dr, cr) in enumerate(lines, start=1):
            self.gl.append({
                "entry_id": entry_id, "line_no": i, "date": d.isoformat(), "account": acct,
                "debit": money(dr), "credit": money(cr), "description": description,
                "counterparty": counterparty, "ref": ref, "source": source, "po_id": po_id,
            })
        return entry_id

    def bank_txn(self, d: date, description: str, amount: float, ref: str = "", counterparty: str = "") -> str:
        bid = self.next_id("BT")
        self.bank.append({"bank_txn_id": bid, "date": d.isoformat(), "description": description,
                          "amount": money(amount), "ref": ref, "counterparty": counterparty})
        return bid

    # ---------------------------------------------------------- business ops
    def customer_invoice(self, d: date, rng: random.Random) -> None:
        cust = rng.choice(CUSTOMERS)
        amount = money(rng.uniform(13_000, 40_000))
        ref = self.next_id("INV", 5)
        lines = [(AR, amount, 0), ("4000", 0, amount)]
        if rng.random() < 0.45:
            svc = money(rng.uniform(2_000, 7_500))
            lines[0] = (AR, money(amount + svc), 0)
            lines.append(("4100", 0, svc))
        self.post(d, lines, f"Invoice {ref} - {cust}", cust, ref, "AR")
        cogs = money(amount * rng.uniform(0.54, 0.59))
        self.post(d, [("5000", cogs, 0), (INVENTORY, 0, cogs)], f"COGS relief {ref}", cust, ref, "AR")
        if rng.random() < 0.15:
            disc = money(rng.uniform(500, 2_400))
            self.post(d, [("4900", disc, 0), (AR, 0, disc)], f"Early-pay discount {ref}", cust, ref, "AR")

    def customer_receipt(self, d: date, rng: random.Random, bank_date: date | None = None) -> None:
        cust = rng.choice(CUSTOMERS)
        amount = money(rng.uniform(10_000, 55_000))
        ref = self.next_id("RCPT")
        self.post(d, [(CASH, amount, 0), (AR, 0, amount)], f"Customer receipt {ref} - {cust}", cust, ref, "CASH")
        self.bank_txn(bank_date or d, f"ACH CREDIT {cust.upper()} {ref}", amount, ref, cust)

    def vendor_bill(self, d: date, vendor: str, amount: float, account: str | None = None,
                    po_id: str = "", description: str | None = None) -> str:
        account = account or VENDORS[vendor]
        ref = self.next_id("BILL")
        self.post(d, [(account, amount, 0), (AP, 0, amount)],
                  description or f"Vendor bill {ref} - {vendor}", vendor, ref, "AP", po_id)
        return ref

    def vendor_payment(self, d: date, vendor: str, amount: float, method: str = "ACH",
                       bank_date: date | None = None, bank_amount: float | None = None,
                       mirror: bool = True) -> str:
        if method == "CHECK":
            ref = self.next_id("CHK", 5)
            desc = f"CHECK #{ref.split('-')[1]}"
        else:
            ref = self.next_id("PAY")
            desc = f"ACH DEBIT {vendor.upper()} {ref}"
        self.post(d, [(AP, amount, 0), (CASH, 0, amount)], f"Payment {ref} - {vendor}", vendor, ref, "CASH")
        if mirror:
            self.bank_txn(bank_date or d, desc, -(bank_amount if bank_amount is not None else amount), ref, vendor)
        return ref

    def cash_expense(self, d: date, account: str, amount: float, counterparty: str, description: str,
                     bank_desc: str, ref: str = "") -> None:
        ref = ref or self.next_id("PAY")
        self.post(d, [(account, amount, 0), (CASH, 0, amount)], description, counterparty, ref, "CASH")
        self.bank_txn(d, bank_desc, -amount, ref, counterparty)

    def purchase_order(self, d: date, vendor: str, amount: float, description: str, account: str | None = None) -> str:
        po_id = self.next_id("PO")
        self.pos.append({"po_id": po_id, "vendor": vendor, "account": account or VENDORS[vendor],
                         "amount": money(amount), "order_date": d.isoformat(), "description": description})
        return po_id

    def receipt(self, d: date, po_id: str, amount: float, note: str = "Received in full") -> str:
        rid = self.next_id("RC")
        self.receipts.append({"receipt_id": rid, "po_id": po_id, "received_date": d.isoformat(),
                              "amount": money(amount), "note": note})
        return rid

    # ----------------------------------------------------------- one day
    def generate_day(self, d: date, rng: random.Random, month_start: date) -> None:
        weekday = d.weekday() < 5
        if d.day == 1:
            self.cash_expense(d, "6200", 28_500, "Harborview Properties", "Office & warehouse rent",
                              "ACH DEBIT HARBORVIEW PROPERTIES RENT")
            self.cash_expense(d, "6800", 6_800, "Granite Mutual Insurance", "Monthly insurance premium",
                              "ACH DEBIT GRANITE MUTUAL INS PREM")
        if d.day == 3:
            self.cash_expense(d, BANK_FEES, 45, "First Harbor Bank", "Monthly account analysis fee",
                              "MONTHLY ANALYSIS FEE")
        if d.day in (2, 7, 12):
            amt = {2: 4_150, 7: 3_320, 12: 2_880}[d.day]
            self.cash_expense(d, "6500", amt, "Meridian IT Services", "SaaS subscriptions",
                              "ACH DEBIT MERIDIAN IT SERVICES")
        if d.day == 9:
            self.vendor_bill(d, "Northeast Power & Gas", 7_240, "6300", description="Utilities - warehouse & office")
        if d.day == 14:
            self.vendor_payment(d, "Northeast Power & Gas", 7_240)
        if d.day == 15 or d.day == last_day(month_start):
            self.cash_expense(d, "6000", 92_500, "ADP Payroll", "Semi-monthly payroll (net)", "ADP PAYROLL FEES ADP - DEBIT")
            self.cash_expense(d, "6100", 20_500, "ADP Payroll", "Payroll taxes & benefits", "ADP TAX ADP - DEBIT")
        if d.day == 25:
            self.vendor_bill(d, "Evergreen Advisory LLP", 15_000, "6700", description="Monthly advisory retainer")

        n_inv = rng.randint(2, 3) if weekday else rng.randint(0, 1)
        for _ in range(n_inv):
            self.customer_invoice(d, rng)
        if weekday:
            for _ in range(rng.randint(1, 3)):
                self.customer_receipt(d, rng)
            for _ in range(rng.randint(1, 3)):
                vendor = rng.choice([v for v, a in VENDORS.items() if a in AP_BILL_RANGES])
                lo, hi = AP_BILL_RANGES[VENDORS[vendor]]
                self.vendor_bill(d, vendor, money(rng.uniform(lo, hi)))
            for _ in range(rng.randint(1, 2)):
                vendor = rng.choice(list(VENDORS))
                method = "CHECK" if rng.random() < 0.2 else "ACH"
                self.vendor_payment(d, vendor, money(rng.uniform(2_000, 15_000)), method)

    # -------------------------------------------------------- mismatches
    def plant_timing_difference(self, d: date, rng: random.Random, amount: float | None = None,
                                vendor: str = "Keystone Packaging", clear_days: int = 4) -> str:
        amount = amount or money(rng.uniform(6_000, 14_000))
        self.vendor_payment(d, vendor, amount, "CHECK", bank_date=d + timedelta(days=clear_days))
        return f"check to {vendor} for {amount:,.2f} written {d:%b %d}, clears {d + timedelta(days=clear_days):%b %d}"

    def plant_duplicate_payment(self, d: date, rng: random.Random, vendor: str = "Harborline Logistics",
                                amount: float | None = None, gap_days: int = 0) -> str:
        amount = amount or money(rng.uniform(3_000, 9_000))
        ref = self.vendor_payment(d, vendor, amount)
        self.bank_txn(d + timedelta(days=gap_days), f"ACH DEBIT {vendor.upper()} {ref}", -amount, ref, vendor)
        return f"{vendor} {ref} for {amount:,.2f} debited twice by the bank"

    def plant_amount_typo(self, d: date, rng: random.Random, vendor: str = "Summit Glassworks",
                          amount: float | None = None, book_amount: float | None = None) -> str:
        bank_amount = amount or money(rng.randint(30, 99) * 100 + rng.randint(1, 9) * 10)
        book_amount = book_amount or transpose_digits(bank_amount, rng)
        self.vendor_payment(d, vendor, book_amount, bank_amount=bank_amount)
        return f"{vendor} payment booked as {book_amount:,.2f} but bank shows {bank_amount:,.2f}"

    def plant_unrecorded_fee(self, d: date, rng: random.Random) -> str:
        desc, amt = rng.choice([("WIRE TRANSFER FEE", 35.00), ("RETURNED ITEM FEE", 15.00),
                                ("FX CONVERSION FEE", 22.50), ("STOP PAYMENT FEE", 30.00)])
        self.bank_txn(d, desc, -amt, "", "First Harbor Bank")
        return f"{desc.lower()} of {amt:,.2f} on the bank statement with no GL entry"

    PLANTERS = ("plant_timing_difference", "plant_duplicate_payment", "plant_amount_typo", "plant_unrecorded_fee")

    # ------------------------------------------------------------ IO
    def write(self, data_dir: Path, month_start: date) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(data_dir / "general_ledger.csv", GL_FIELDS, sorted(self.gl, key=lambda r: (r["date"], r["entry_id"], r["line_no"])))
        _write_csv(data_dir / "bank_statement.csv", BANK_FIELDS, sorted(self.bank, key=lambda r: (r["date"], r["bank_txn_id"])))
        _write_csv(data_dir / "purchase_orders.csv", PO_FIELDS, self.pos)
        _write_csv(data_dir / "receipts.csv", RECEIPT_FIELDS, self.receipts)
        _write_csv(data_dir / "budget.csv", BUDGET_FIELDS,
                   [{"account": a.number, "month": month_start.strftime("%Y-%m"), "amount": a.budget} for a in CHART if a.is_pnl])
        _write_csv(data_dir / "chart_of_accounts.csv", ACCOUNT_FIELDS,
                   [{"number": a.number, "name": a.name, "type": a.type, "forecast_method": a.forecast_method} for a in CHART])

    @classmethod
    def read(cls, data_dir: Path) -> "Ledger":
        led = cls()
        led.gl = _read_csv(data_dir / "general_ledger.csv")
        led.bank = _read_csv(data_dir / "bank_statement.csv")
        led.pos = _read_csv(data_dir / "purchase_orders.csv")
        led.receipts = _read_csv(data_dir / "receipts.csv")
        for rows, cols in ((led.gl, ("entry_id", "ref")), (led.bank, ("bank_txn_id", "ref")),
                           (led.pos, ("po_id",)), (led.receipts, ("receipt_id",))):
            for r in rows:
                for c in cols:
                    m = re.fullmatch(r"([A-Z]+)-(\d+)", r.get(c) or "")
                    if m and m.group(1) in led.counters:
                        led.counters[m.group(1)] = max(led.counters[m.group(1)], int(m.group(2)))
        return led


def last_day(month_start: date) -> int:
    from .config import days_in_month
    return days_in_month(month_start)


def transpose_digits(amount: float, rng: random.Random) -> float:
    """Swap two adjacent non-equal digits in the whole-dollar part (a classic keying error)."""
    s = str(int(amount))
    idx = [i for i in range(len(s) - 1) if s[i] != s[i + 1]]
    i = rng.choice(idx) if idx else 0
    swapped = s[:i] + s[i + 1] + s[i] + s[i + 2:]
    return money(int(swapped) + (amount - int(amount)))


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- seed
def generate_seed(data_dir: Path = DATA_DIR, month_start: date | None = None,
                  as_of_day: int = SEED_AS_OF_DAY, seed: int = 42) -> State:
    month_start = month_start or current_month()
    led = Ledger()
    ms = month_start
    D = lambda day: ms + timedelta(days=day - 1)  # noqa: E731

    # Purchase orders (expense POs that drive the GRNI accrual) ------------
    po1 = led.purchase_order(D(1), "Harborline Logistics", 14_200, "Inbound freight - week 1")
    po2 = led.purchase_order(D(4), "Blue Ridge Print Co.", 18_500, "Q4 retail point-of-sale displays")
    po3 = led.purchase_order(D(8), "Northgate Freight", 9_860, "Inbound freight - cold chain")
    po4 = led.purchase_order(D(12), "Clearwater Office Supply", 1_240, "Warehouse office supplies")
    po5 = led.purchase_order(D(5), "Lakeside Media Group", 22_000, "Regional radio campaign (2 flights)")
    po6 = led.purchase_order(D(2), "Meridian IT Services", 4_800, "Warehouse scanner firmware upgrade")
    po7 = led.purchase_order(D(3), "Harborline Logistics", 7_940, "Inbound freight - week 2")
    po8 = led.purchase_order(D(6), "Keystone Packaging", 12_480, "Shrink wrap & case trays")
    led.purchase_order(D(15), "Northgate Freight", 6_300, "Inbound freight - week 4")
    led.purchase_order(D(17), "Lakeside Media Group", 15_000, "Digital retargeting - November")

    led.receipt(D(6), po1, 14_200)
    led.receipt(D(10), po2, 18_500)
    led.receipt(D(14), po3, 9_860)
    led.receipt(D(16), po4, 1_240)
    led.receipt(D(12), po5, 11_000, "Flight 1 of 2 aired")
    led.receipt(D(4), po6, 4_800)
    led.receipt(D(7), po7, 7_940)
    led.receipt(D(9), po8, 12_480)

    for day in range(1, as_of_day + 1):
        d = D(day)
        rng = random.Random(seed * 1000 + day)
        led.generate_day(d, rng, ms)
        if day == 9:
            led.vendor_bill(d, "Harborline Logistics", 14_200, po_id=po1, description="Freight invoice - PO-2401")
        if day == 8:
            led.vendor_bill(d, "Meridian IT Services", 4_800, po_id=po6, description="Firmware upgrade - PO-2406")
            led.vendor_bill(d, "Harborline Logistics", 7_940, po_id=po7, description="Freight invoice - PO-2407")
        if day == 10:
            led.vendor_bill(d, "Keystone Packaging", 12_480, po_id=po8, description="Packaging - PO-2408")
        # planted problems
        if day == 11:
            led.plant_duplicate_payment(d, rng, "Harborline Logistics", 7_940, gap_days=1)
        if day == 12:
            led.plant_unrecorded_fee(d, random.Random(7))  # wire fee
        if day == 13:
            led.plant_amount_typo(d, rng, "Summit Glassworks", 7_850, book_amount=8_750)
        if day == 15:
            led.plant_timing_difference(d, rng, 12_480, "Keystone Packaging", clear_days=4)

    led.write(data_dir, ms)
    state = State(ms, D(as_of_day))
    save_state(state, data_dir)
    return state


# ---------------------------------------------------------- simulate
def simulate_next_day(data_dir: Path = DATA_DIR, seed: int | None = None) -> dict:
    """Append one more business day of activity, including 1-2 fresh mismatches."""
    from .config import load_state
    state = load_state(data_dir)
    if state.as_of >= state.month_end:
        raise ValueError("Already at month end; run `cc generate` to reset the month.")
    new_day = state.as_of + timedelta(days=1)
    led = Ledger.read(data_dir)
    rng = random.Random(seed if seed is not None else new_day.toordinal())
    before = (len(led.gl), len(led.bank))
    led.generate_day(new_day, rng, state.month_start)

    # Move a PO along: invoice one GRNI item or receive an open PO.
    po_events: list[str] = []
    invoiced = {r["po_id"] for r in led.gl if r.get("po_id")}
    received = {r["po_id"] for r in led.receipts}
    grni = [p for p in led.pos if p["po_id"] in received and p["po_id"] not in invoiced]
    open_pos = [p for p in led.pos if p["po_id"] not in received]
    if grni and rng.random() < 0.6:
        p = rng.choice(grni)
        recd = sum(float(r["amount"]) for r in led.receipts if r["po_id"] == p["po_id"])
        led.vendor_bill(new_day, p["vendor"], recd, p["account"], p["po_id"], f"{p['description']} - {p['po_id']}")
        po_events.append(f"invoice received for {p['po_id']} ({p['vendor']}, {recd:,.2f}) - accrual reverses")
    elif open_pos:
        p = rng.choice(open_pos)
        led.receipt(new_day, p["po_id"], float(p["amount"]))
        po_events.append(f"goods received on {p['po_id']} ({p['vendor']}, {float(p['amount']):,.2f}) - new accrual")

    planted: list[str] = []
    for name in rng.sample(Ledger.PLANTERS, k=rng.randint(1, 2)):
        if name == "plant_timing_difference":
            planted.append(led.plant_timing_difference(new_day, rng, vendor=rng.choice(list(VENDORS)), clear_days=rng.randint(3, 5)))
        elif name == "plant_duplicate_payment":
            planted.append(led.plant_duplicate_payment(new_day, rng, vendor=rng.choice(list(VENDORS))))
        elif name == "plant_amount_typo":
            planted.append(led.plant_amount_typo(new_day, rng, vendor=rng.choice(list(VENDORS))))
        else:
            planted.append(led.plant_unrecorded_fee(new_day, rng))

    led.write(data_dir, state.month_start)
    new_state = State(state.month_start, new_day)
    save_state(new_state, data_dir)
    return {
        "as_of": new_day,
        "new_gl_lines": len(led.gl) - before[0],
        "new_bank_txns": len(led.bank) - before[1],
        "planted": planted,
        "po_events": po_events,
    }
