from __future__ import annotations

import calendar
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

COMPANY = "Brightwater Beverages"
SEGMENT = "Northeast Wholesale"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CC_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("CC_DB_PATH", ROOT / "continuous_close.duckdb"))
STATE_FILE = "state.json"
SEED_AS_OF_DAY = 18


def current_month() -> date:
    """First day of the fiscal month. Defaults to the real current month; override with CC_MONTH=YYYY-MM."""
    override = os.environ.get("CC_MONTH")
    if override:
        y, m = override.split("-")
        return date(int(y), int(m), 1)
    today = date.today()
    return date(today.year, today.month, 1)


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


@dataclass
class State:
    month_start: date
    as_of: date

    @property
    def day(self) -> int:
        return self.as_of.day

    @property
    def days_in_month(self) -> int:
        return days_in_month(self.month_start)

    @property
    def month_end(self) -> date:
        return date(self.month_start.year, self.month_start.month, self.days_in_month)

    @property
    def pct_elapsed(self) -> float:
        return self.day / self.days_in_month

    def to_json(self) -> dict:
        return {"month_start": self.month_start.isoformat(), "as_of": self.as_of.isoformat()}


def load_state(data_dir: Path = DATA_DIR) -> State:
    p = data_dir / STATE_FILE
    if not p.exists():
        raise FileNotFoundError(f"No data found in {data_dir}. Run `cc generate` first.")
    raw = json.loads(p.read_text())
    return State(date.fromisoformat(raw["month_start"]), date.fromisoformat(raw["as_of"]))


def save_state(state: State, data_dir: Path = DATA_DIR) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / STATE_FILE).write_text(json.dumps(state.to_json(), indent=2) + "\n")
