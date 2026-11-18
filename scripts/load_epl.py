"""Load data/raw/epl_season_2023-2024.json into DuckDB as raw.epl_season.

The JSON is stored as-is (one row with an 'events' list), like the raw S3 object
the paper's stg_matches read. Unnesting happens in the dbt staging model.
Run from the data_eng folder (close any other program using the database first):
    uv run python scripts/load_epl.py
"""
import os
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

SRC = Path("data/raw/epl_season_2023-2024.json")
DB = Path(os.getenv("DUCKDB_PATH", "data/warehouse.duckdb"))
REQUIRED = ["idEvent", "idLeague", "strSeason", "dateEvent", "strStatus", "idHomeTeam",
            "strHomeTeam", "intHomeScore", "idAwayTeam", "strAwayTeam", "intAwayScore"]


def main() -> int:
    if not SRC.exists():
        print(f"Missing {SRC}. Run: uv run python scripts/download_epl.py")
        return 1
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB))
    try:
        con.execute("create schema if not exists raw")
        con.execute("create or replace table raw.epl_season as select * from read_json_auto(?)",
                    [SRC.as_posix()])
        n_events = con.execute("select len(events) from raw.epl_season").fetchone()[0]
        fields = [r[0] for r in con.execute(
            "describe select unnest(events, recursive := true) from raw.epl_season").fetchall()]
    finally:
        con.close()

    missing = [c for c in REQUIRED if c not in fields]
    print(f"Loaded raw.epl_season into {DB}: {n_events} events, {len(fields)} fields per event")
    if missing:
        print(f"FAIL: events are missing fields {missing}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
