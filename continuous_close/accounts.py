"""Chart of accounts and monthly budget for the demo segment."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    number: str
    name: str
    type: str  # revenue | cogs | opex | asset | liability
    forecast_method: str  # run_rate | budget_fill | none
    budget: float  # monthly budget (natural sign: revenue and expenses positive)

    @property
    def is_pnl(self) -> bool:
        return self.type in ("revenue", "cogs", "opex")

    @property
    def is_expense(self) -> bool:
        return self.type in ("cogs", "opex")


CHART = [
    # Revenue — forecast on daily run-rate
    Account("4000", "Product Revenue", "revenue", "run_rate", 1_450_000),
    Account("4100", "Service Revenue", "revenue", "run_rate", 95_000),
    Account("4900", "Discounts & Returns", "revenue", "run_rate", -48_000),
    # Cost of sales — run-rate (moves with revenue)
    Account("5000", "Cost of Goods Sold", "cogs", "run_rate", 820_000),
    Account("5100", "Freight In", "cogs", "run_rate", 62_000),
    # Operating expenses
    Account("6000", "Salaries & Wages", "opex", "budget_fill", 185_000),
    Account("6100", "Payroll Taxes & Benefits", "opex", "budget_fill", 41_000),
    Account("6200", "Rent", "opex", "budget_fill", 28_500),
    Account("6300", "Utilities", "opex", "budget_fill", 7_200),
    Account("6400", "Marketing & Advertising", "opex", "run_rate", 45_000),
    Account("6500", "Software & Subscriptions", "opex", "budget_fill", 12_400),
    Account("6600", "Travel & Entertainment", "opex", "run_rate", 9_500),
    Account("6700", "Professional Fees", "opex", "budget_fill", 15_000),
    Account("6800", "Insurance", "opex", "budget_fill", 6_800),
    Account("6900", "Bank Fees", "opex", "run_rate", 450),
    Account("6950", "Office Supplies", "opex", "run_rate", 2_200),
    # Balance sheet
    Account("1000", "Cash - Operating", "asset", "none", 0),
    Account("1200", "Accounts Receivable", "asset", "none", 0),
    Account("1300", "Inventory", "asset", "none", 0),
    Account("2000", "Accounts Payable", "liability", "none", 0),
    Account("2100", "Accrued Liabilities", "liability", "none", 0),
]

BY_NUMBER = {a.number: a for a in CHART}
CASH = "1000"
AR = "1200"
INVENTORY = "1300"
AP = "2000"
ACCRUED = "2100"
BANK_FEES = "6900"
