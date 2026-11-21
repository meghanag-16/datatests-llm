"""Check that the fast scoring engine gives the same test statuses as dbt.

Compiles the candidate tests once, runs them with the fast engine on the clean snapshot and on
mutated databases, and compares with dbt:
  - every mutant whose saved result for this exact test set was produced by dbt
    (reports/scoring/.../runs/*.json written by "scoring.run --engine dbt" or earlier versions), and
  - a fresh dbt run on --dbt-sample mutants (spread over all operators) plus the clean snapshot.
A test counts as catching a mutant when its status is fail or error, as in scoring.run; the
check compares exact statuses and reports any difference.

Run from the data_eng folder, with no other run using the dataset:
  uv run python -m scoring.verify_fast --dataset olist --test-set manual_baseline.yml llm_gemini.yml
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from common import datasets
from common.dbt_runner import active_test_set, fresh_copy
from llm_tests.validate import to_yaml
from scoring import fast
from scoring.run import load_candidates, resolve, run_on, test_set_hash


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="football", choices=list(datasets.DATASETS))
    p.add_argument("--test-set", nargs="+", required=True)
    p.add_argument("--name", help="scoring output name whose saved dbt results to compare with")
    p.add_argument("--dbt-sample", type=int, default=14, help="mutants to run fresh with dbt (0 = none)")
    args = p.parse_args()
    ds = datasets.get(args.dataset)

    paths = [resolve(t, ds.test_sets) for t in args.test_set]
    name = args.name or "__".join(x.stem for x in paths)
    out = ds.report_dir("scoring") / name
    runs_dir = out / "runs"
    work = ds.report_dir("scoring") / "_verify_fast"
    work.mkdir(parents=True, exist_ok=True)

    specs, _, _ = load_candidates(paths, ds)
    text = to_yaml(specs, list(ds.models), "# All candidate tests for scoring.")
    candidate_file = work / "candidates.yml"
    candidate_file.write_text(text, encoding="utf-8", newline="\n")
    set_hash = test_set_hash(text)
    mutants = [json.loads(x) for x in ds.manifest.read_text(encoding="utf-8").splitlines() if x.strip()]
    mutants = [m for m in mutants if m.get("status") == "ok" and datasets.resolve_file(m["file"]).exists()]

    # Saved dbt results for this exact test set.
    saved = {}
    for m in mutants:
        f = runs_dir / f"{m['mutation_id']}.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("test_set_hash") == set_hash and data.get("engine", "dbt") == "dbt":
                saved[m["mutation_id"]] = data["status"]

    # A spread of mutants for a fresh dbt run: round-robin over operators.
    by_op: dict[str, list[dict]] = {}
    for m in mutants:
        if m["mutation_id"] not in saved:
            by_op.setdefault(m["operator"], []).append(m)
    sample = []
    while len(sample) < args.dbt_sample and any(by_op.values()):
        for op in sorted(by_op):
            if by_op[op] and len(sample) < args.dbt_sample:
                sample.append(by_op[op].pop(len(by_op[op]) // 2))

    t0 = time.time()
    with active_test_set(candidate_file, ds.active_schema):
        compiled = fast.compile_tests(ds, f"{ds.name}_verify_compile", work / "logs")
        reference = {}
        if args.dbt_sample:
            print(f"Running dbt on the clean snapshot and {len(sample)} mutants ...")
            clean_copy = fresh_copy(ds.snapshot, f"{ds.name}_verify_clean")
            reference["clean"] = (ds.snapshot, run_on(clean_copy, f"{ds.name}_verify_clean", work / "logs", ds))
            for m in sample:
                reference[m["mutation_id"]] = (datasets.resolve_file(m["file"]),
                                              run_on(datasets.resolve_file(m["file"]),
                                                     f"{ds.name}_verify_{m['mutation_id']}", work / "logs", ds))
    dbt_time = time.time() - t0
    for m in mutants:
        if m["mutation_id"] in saved:
            reference[m["mutation_id"]] = (datasets.resolve_file(m["file"]), saved[m["mutation_id"]])

    print(f"Compiled {len(compiled)} tests. Comparing with dbt on {len(reference)} databases "
          f"({len(saved)} saved dbt results, {len(reference) - len(saved)} fresh dbt runs) ...")
    t1 = time.time()
    differences = []
    compared = 0
    for key, (db, ref) in reference.items():
        got = fast.run_tests(db, compiled)
        for tid in sorted(set(ref) | set(got)):
            compared += 1
            if ref.get(tid) != got.get(tid):
                differences.append((key, tid, ref.get(tid), got.get(tid)))
    fast_time = time.time() - t1

    print(f"Compared {compared} test results: {len(differences)} differences.")
    for key, tid, a, b in differences[:30]:
        print(f"  {key}: {tid}: dbt={a} fast={b}")
    if args.dbt_sample and sample:
        print(f"Time for the fresh part: dbt {dbt_time:.0f}s vs fast {fast_time:.1f}s for all {len(reference)} databases.")
    if differences:
        print("FAIL: the fast engine does not match dbt; use --engine dbt for scoring.")
        return 1
    print("OK: the fast engine gives exactly the same statuses as dbt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
