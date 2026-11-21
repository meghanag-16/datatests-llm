"""Build a dataset's dbt models into its own DuckDB file (dbt build --full-refresh).

Sets the database path and the dbt variables for the dataset, so the same dbt project can
build either dataset:
    uv run python scripts/build_dataset.py                    # football -> data/warehouse.duckdb
    uv run python scripts/build_dataset.py --dataset olist    # olist    -> data/olist.duckdb
Extra arguments after -- go to dbt, for example: -- --vars "{dataset: olist, olist_orders: 0}"

The build holds the dataset's test-set lock, so it never runs while a scoring or generation run
has its tests swapped in, and it first puts back a schema.yml left behind by a stopped run.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from data_eng
from common import datasets  # noqa: E402
from common.dbt_runner import protected_schema  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="football", choices=list(datasets.DATASETS))
    p.add_argument("dbt_extra", nargs=argparse.REMAINDER, help="extra dbt arguments after --")
    args = p.parse_args()
    ds = datasets.get(args.dataset)

    dbt = shutil.which("dbt")
    if not dbt:
        print("dbt not found. Run this with 'uv run' from the data_eng folder.")
        return 1
    extra = [a for a in args.dbt_extra if a != "--"]
    cmd = [dbt, "build", "--project-dir", str(datasets.DBT_DIR), "--profiles-dir", str(datasets.DBT_DIR),
           "--full-refresh"]
    if not any(a == "--vars" for a in extra):
        cmd += ds.dbt_args()
    cmd += extra
    env = os.environ.copy()
    env["DUCKDB_PATH"] = ds.warehouse.as_posix()
    print(f"Building {ds.name} into {ds.warehouse.relative_to(datasets.ROOT)}")
    print("$ dbt " + " ".join(cmd[1:]), flush=True)
    with protected_schema(ds.active_schema):
        return subprocess.run(cmd, cwd=str(datasets.ROOT), env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
