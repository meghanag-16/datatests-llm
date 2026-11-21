"""Save a clean copy of a dataset's DuckDB warehouse after a successful dbt build.

Every later experiment restores from this file, so the clean data is never touched.
  football  the whole warehouse is copied to data/snapshots/dbt.clean.duckdb (the file name
            follows the source paper).
  olist     only the tested models are copied, as tables, into data/snapshots/olist.clean.duckdb.
            The raw and staging tables are left out so that the hundreds of mutated copies
            made from the snapshot stay small.
Run from the data_eng folder, after dbt build, with no other program using the database:
    uv run python scripts/snapshot_clean.py
    uv run python scripts/snapshot_clean.py --dataset olist
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from data_eng
from common import datasets  # noqa: E402

load_dotenv()

FOOTBALL_TABLES = ["raw.epl_season", "main.stg_matches", "main.dim_teams",
                   "main.fct_matches", "main.fct_training_dataset"]


def counts(path: Path, tables: list[str]) -> dict:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
    finally:
        con.close()


def copy_models(db: Path, snapshot: Path, models: list[str]) -> None:
    """Write the given models from db into a new, compact snapshot file."""
    tmp = snapshot.with_name(snapshot.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        con.execute(f"attach '{db.as_posix()}' as src (read_only)")
        for m in models:
            # A fixed row order: mutations pick rows by position, so the same data must always
            # be stored in the same order for the mutated databases to be reproducible.
            con.execute(f'create table main."{m}" as select * from src.main."{m}" order by all')
        con.execute("detach src")
        con.execute("checkpoint")
    finally:
        con.close()
    os.replace(tmp, snapshot)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="football", choices=list(datasets.DATASETS))
    args = p.parse_args()
    ds = datasets.get(args.dataset)

    if ds.name == "football":
        db = Path(os.getenv("DUCKDB_PATH", "data/warehouse.duckdb"))
        tables = FOOTBALL_TABLES
    else:
        db = ds.warehouse
        tables = [f"main.{m}" for m in ds.models]
    snapshot = ds.snapshot
    if not db.exists():
        print(f"Missing {db}. Load the data and run dbt build first.")
        return 1
    con = duckdb.connect(str(db))
    con.execute("checkpoint")  # flush everything into the main file before copying
    con.close()

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists():
        print(f"Replacing existing {snapshot.relative_to(datasets.ROOT)}")
    if ds.name == "football":
        shutil.copyfile(db, snapshot)   # contents only (copying file times can fail on mounted folders)
    else:
        copy_models(db, snapshot, list(ds.models))

    src, snap = counts(db, tables), counts(snapshot, tables)
    for t in tables:
        print(f"  {t:28s} warehouse={src[t]:6d} snapshot={snap[t]:6d}")
    if src != snap:
        print("FAIL: snapshot row counts differ from the warehouse")
        return 1
    print(f"OK: clean snapshot saved to {snapshot.relative_to(datasets.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
