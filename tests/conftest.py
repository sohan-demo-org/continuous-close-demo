from datetime import date
from pathlib import Path

import pytest

from continuous_close.datagen import generate_seed
from continuous_close.pipeline import run_pipeline

MONTH = date(2026, 10, 1)


@pytest.fixture(scope="session")
def workspace(tmp_path_factory) -> tuple[Path, Path]:
    """Seed data + a fully run pipeline in a temp dir, shared across tests."""
    root = tmp_path_factory.mktemp("cc")
    data_dir, db_path = root / "data", root / "cc.duckdb"
    generate_seed(data_dir, MONTH, as_of_day=18)
    run_pipeline(data_dir, db_path)
    return data_dir, db_path
