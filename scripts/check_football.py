"""Sanity checks on the football models (not dbt tests; those come later).

Compares row counts with the paper (fct_matches 100, dim_teams 20,
fct_training_dataset 100) and checks basic integrity. Run from the data_eng folder:
    uv run python scripts/check_football.py               # warehouse
    uv run python scripts/check_football.py --snapshot    # clean snapshot
"""
import argparse
import os
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

PAPER = {"dim_teams": 20, "fct_matches": 100, "fct_training_dataset": 100}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", action="store_true", help="check data/snapshots/dbt.clean.duckdb")
    args = p.parse_args()

    db = Path("data/snapshots/dbt.clean.duckdb") if args.snapshot else Path(
        os.getenv("DUCKDB_PATH", "data/warehouse.duckdb"))
    if not db.exists():
        print(f"Missing {db}")
        return 1
    con = duckdb.connect(str(db), read_only=True)
    q = lambda sql: con.execute(sql).fetchone()[0]

    counts = {t: q(f"select count(*) from {t}") for t in ["stg_matches", *PAPER]}
    print("Row counts (paper in brackets):")
    print(f"  stg_matches            {counts['stg_matches']:4d}  (100)")
    for t, want in PAPER.items():
        print(f"  {t:22s} {counts[t]:4d}  ({want})")

    checks = [
        ("fct_matches only has finished matches",
         q("select count(*) from fct_matches where match_status <> 'Match Finished'") == 0),
        ("match_id unique and not null in fct_matches",
         q("select count(distinct match_id) from fct_matches") == counts["fct_matches"]),
        ("team_id unique and not null in dim_teams",
         q("select count(distinct team_id) from dim_teams") == counts["dim_teams"]),
        ("one name per team_id", q("select count(distinct team_name) from dim_teams") == counts["dim_teams"]),
        ("every match team exists in dim_teams", q("""
            select count(*) from fct_matches m
            where home_team_id not in (select team_id from dim_teams)
               or away_team_id not in (select team_id from dim_teams)""") == 0),
        ("no team plays itself", q("select count(*) from fct_matches where home_team_id = away_team_id") == 0),
        ("scores and dates present", q("""
            select count(*) from fct_matches
            where home_team_score is null or away_team_score is null or match_date is null""") == 0),
        ("training rows equal fct_matches rows", counts["fct_training_dataset"] == counts["fct_matches"]),
    ]
    con.close()

    failed = 0
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        failed += not ok
    same = all(counts[t] == want for t, want in PAPER.items())
    print(f"{len(checks) - failed}/{len(checks)} checks passed on {db}")
    print("Row counts match the paper." if same else "Row counts differ from the paper (see above).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
