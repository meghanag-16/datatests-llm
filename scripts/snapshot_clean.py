"""Save a clean copy of the DuckDB warehouse after a successful dbt build.

Every later experiment restores from this file, so the clean data is never
touched. The file name follows the source paper (dbt.clean.duckdb).
Run from the data_eng folder, after dbt build, with no other program using the database:
    uv run python scripts/snapshot_clean.py
"""
import os
import shutil
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

DB = Path(os.getenv("DUCKDB_PATH", "data/warehouse.duckdb"))
SNAPSHOT = Path("data/snapshots/dbt.clean.duckdb")
TABLES = ["raw.epl_season", "main.stg_matches", "main.dim_teams",
          "main.fct_matches", "main.fct_training_dataset"]


def counts(path: Path) -> dict:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in TABLES}
    finally:
        con.close()


def main() -> int:
    if not DB.exists():
        print(f"Missing {DB}. Load the data and run dbt build first.")
        return 1
    con = duckdb.connect(str(DB))
    con.execute("checkpoint")  # flush everything into the main file before copying
    con.close()

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    if SNAPSHOT.exists():
        print(f"Replacing existing {SNAPSHOT}")
    shutil.copy2(DB, SNAPSHOT)

    src, snap = counts(DB), counts(SNAPSHOT)
    for t in TABLES:
        print(f"  {t:28s} warehouse={src[t]:4d} snapshot={snap[t]:4d}")
    if src != snap:
        print("FAIL: snapshot row counts differ from the warehouse")
        return 1
    print(f"OK: clean snapshot saved to {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
