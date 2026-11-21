"""Create mutated copies of the clean snapshot, one damaged database per mutation.

Each copy is checked against the clean data: a mutation only counts if it changed at least
one row and the affected table no longer matches the clean table. Every mutation is written
to a manifest (operator, table, column, variant, rows, seed, rows changed), so any result
can be reproduced from it.

Run from the data_eng folder:
  uv run python -m mutations.run                       # all operators, football config
  uv run python -m mutations.run --plan-only           # list what would be created
  uv run python -m mutations.run --operators null_injection date_shift
  uv run python -m mutations.run --dataset olist          # data/mutants/olist/
Outputs: data/mutants/<mutation_id>.duckdb and data/mutants/manifest.jsonl (football)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from common import datasets
from mutations.catalog import build_catalog, load_config
from mutations.operators import OPERATORS, apply
from mutations.plan import plan_mutations

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(con, table: str) -> tuple[int, int]:
    """Row count and an order-independent hash of every row."""
    n, h = con.execute(f'select count(*), coalesce(sum(hash(t)), 0) from "{table}" t').fetchone()
    return int(n), int(h)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="football", choices=list(datasets.DATASETS))
    p.add_argument("--snapshot", type=Path, help="clean database (default: the dataset's snapshot)")
    p.add_argument("--config", type=Path, help="mutation config (default: mutations/config/<dataset>.yml)")
    p.add_argument("--out", type=Path, help="output folder (default: the dataset's mutants folder)")
    p.add_argument("--operators", nargs="+", choices=list(OPERATORS))
    p.add_argument("--limit", type=int, help="create at most this many mutants")
    p.add_argument("--plan-only", action="store_true")
    args = p.parse_args()
    ds = datasets.get(args.dataset)
    args.snapshot = args.snapshot or ds.snapshot
    args.config = args.config or ds.mutation_config
    args.out = args.out or ds.mutants

    if not args.snapshot.exists():
        print(f"Missing {args.snapshot}. Run: uv run python scripts/snapshot_clean.py --dataset {ds.name}")
        return 1
    config = load_config(args.config)
    clean = duckdb.connect(str(args.snapshot), read_only=True)
    try:
        cat = build_catalog(clean, config)
        clean_prints = {t: fingerprint(clean, t) for t in cat.tables}
    finally:
        clean.close()

    print(f"Tables: {', '.join(f'{t} ({n} rows)' for t, n in cat.tables.items())}")
    roles = defaultdict(list)
    for c in cat.columns:
        roles[c.role].append(f"{c.table}.{c.name}")
    for role in ("key", "foreign", "category", "numeric", "date", "other"):
        if roles[role]:
            print(f"  {role:9s} {', '.join(roles[role])}")

    plan = plan_mutations(cat, args.operators)
    if args.limit:
        plan = plan[: args.limit]
    print(f"\nPlanned mutations: {len(plan)}")
    for op, n in Counter(m.operator for m in plan).items():
        print(f"  {op:18s} {n}")
    if args.plan_only:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    records, start = [], time.time()
    status_count: Counter = Counter()
    for i, m in enumerate(plan, 1):
        path = args.out / f"{m.mutation_id}.duckdb"
        shutil.copyfile(args.snapshot, path)
        con = duckdb.connect(str(path))
        try:
            changed = apply(con, m)
            touched = [m.table] + ([m.parent_table] if m.parent_table else [])
            differs = any(fingerprint(con, t) != clean_prints[t] for t in touched)
            con.execute("checkpoint")
        except duckdb.Error as exc:
            changed, differs = 0, False
            print(f"  error in {m.mutation_id}: {str(exc).splitlines()[0][:150]}")
        finally:
            con.close()
        status = "ok" if changed > 0 and differs else "no_effect"
        status_count[(m.operator, status)] += 1
        records.append({**m.to_dict(), "rows_changed": changed, "differs_from_clean": differs,
                        "status": status, "file": path.relative_to(ROOT).as_posix()})
        if i % 25 == 0 or i == len(plan):
            print(f"  {i}/{len(plan)} done ({time.time() - start:.0f}s)")

    manifest = args.out / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")

    ok = sum(1 for r in records if r["status"] == "ok")
    print(f"\nCreated {ok} mutated databases; {len(records) - ok} mutations had no effect (marked no_effect in the manifest; later steps skip them).")
    for op in dict.fromkeys(m.operator for m in plan):
        print(f"  {op:18s} ok {status_count[(op, 'ok')]:4d}   no effect {status_count[(op, 'no_effect')]:3d}")
    print(f"Manifest: {manifest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
