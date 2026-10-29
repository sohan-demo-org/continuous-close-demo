"""`cc` command line: generate | pipeline | summary | simulate-day | serve | demo."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import typer

from . import config

app = typer.Typer(help="Continuous Close - every morning: if the month ended today, where would we be, and where will we finish?",
                  no_args_is_help=True, add_completion=False)


def _month(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    y, m = value.split("-")
    return date(int(y), int(m), 1)


@app.command()
def generate(month: Optional[str] = typer.Option(None, help="Fiscal month YYYY-MM (default: current month)"),
             as_of_day: int = typer.Option(config.SEED_AS_OF_DAY, help="Generate activity through this day"),
             data_dir: Path = typer.Option(config.DATA_DIR)):
    """(Re)generate the synthetic source data: GL, bank statement, POs/receipts, budget."""
    from .datagen import generate_seed
    state = generate_seed(data_dir, _month(month), as_of_day)
    typer.echo(f"Generated {state.month_start:%B %Y} through day {state.day} into {data_dir}")


@app.command()
def pipeline(data_dir: Path = typer.Option(config.DATA_DIR), db_path: Path = typer.Option(config.DB_PATH),
             no_backfill: bool = typer.Option(False, help="Only compute today's snapshot instead of every day so far")):
    """Daily pipeline: load sources into DuckDB, reconcile, accrue, project, snapshot."""
    from .pipeline import run_pipeline
    r = run_pipeline(data_dir, db_path, backfill=not no_backfill)
    typer.echo(f"Pipeline complete as of {r['as_of']}: {r['gl_lines']} GL lines, {r['bank_txns']} bank txns, "
               f"{r['matched']} auto-cleared, {r['mismatches']} mismatches, {r['accruals']} accruals -> {db_path}")


@app.command()
def summary(out: Optional[Path] = typer.Option(None, help="Write the markdown here instead of stdout"),
            db_path: Path = typer.Option(config.DB_PATH)):
    """Write the morning summary (markdown)."""
    from .summary import build_summary
    md = build_summary(db_path)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        typer.echo(f"Wrote {out}")
    else:
        typer.echo(md, nl=False)


@app.command("simulate-day")
def simulate_day(data_dir: Path = typer.Option(config.DATA_DIR), db_path: Path = typer.Option(config.DB_PATH),
                 seed: Optional[int] = typer.Option(None, help="RNG seed for reproducible days"),
                 no_pipeline: bool = typer.Option(False, help="Append the data but don't re-run the pipeline")):
    """Advance one day: new invoices, receipts, bills and payments - plus 1-2 fresh mismatches."""
    from .datagen import simulate_next_day
    r = simulate_next_day(data_dir, seed)
    typer.echo(f"Advanced to {r['as_of']}: +{r['new_gl_lines']} GL lines, +{r['new_bank_txns']} bank txns")
    for p in r["po_events"]:
        typer.echo(f"  PO event: {p}")
    for p in r["planted"]:
        typer.echo(f"  New problem: {p}")
    if not no_pipeline:
        from .pipeline import run_pipeline
        res = run_pipeline(data_dir, db_path)
        typer.echo(f"Pipeline re-run: {res['matched']} auto-cleared, {res['mismatches']} mismatches, {res['accruals']} accruals")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Run the dashboard."""
    import uvicorn
    uvicorn.run("continuous_close.web.app:app", host=host, port=port, reload=reload)


@app.command()
def demo(month: Optional[str] = typer.Option(None, help="Fiscal month YYYY-MM"),
         data_dir: Path = typer.Option(config.DATA_DIR), db_path: Path = typer.Option(config.DB_PATH)):
    """generate + pipeline + summary in one go."""
    from .datagen import generate_seed
    from .pipeline import run_pipeline
    from .summary import build_summary
    generate_seed(data_dir, _month(month))
    run_pipeline(data_dir, db_path)
    typer.echo(build_summary(db_path), nl=False)


if __name__ == "__main__":
    app()
