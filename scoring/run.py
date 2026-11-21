"""Score tests against every mutated database, prune them, and evaluate on held-out damage.

Steps
  1. Combine the given test sets into one candidate set (identical tests merged; the
     earlier test set wins ties, so list the manual baseline first).
  2. Run dbt test on the clean snapshot: tests that fail there are marked broken.
  3. Run dbt test on every mutant from data/mutants/manifest.jsonl. A test catches a
     mutant when it fails (or errors) on it. Results are saved per mutant, so an
     interrupted run continues where it stopped.
  4. Classify tests (useful / redundant / low_value / broken) over all mutants and pick
     a minimal set with greedy set cover. The minimal set is saved as a test set.
  5. Held-out evaluation: choose the minimal set on some operators only and measure it
     on the others (fixed split from scoring/config.yml plus leave-one-operator-out).

Engines (--engine): "fast" (default) compiles the tests once with dbt and runs the compiled SQL
directly in DuckDB on every mutant (scoring/fast.py); "dbt" runs dbt test on every mutant.
Both give the same statuses (check with scoring/verify_fast.py) and share saved results.

Run from the data_eng folder:
  uv run python -m scoring.run --test-set manual_baseline.yml llm_gemini.yml
  uv run python -m scoring.run --test-set manual_baseline.yml llm_groq.yml --workers 4
  uv run python -m scoring.run --dataset olist --test-set manual_baseline.yml llm_gemini.yml
Outputs: reports/scoring/<name>/ and dbt_project/test_sets/pruned_<name>.yml
(other datasets: reports/scoring/<dataset>/<name>/ and dbt_project/test_sets/<dataset>/)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from common import datasets
from common.dbt_runner import active_test_set, fresh_copy, run_dbt
from llm_tests.context import load_contexts
from llm_tests.validate import TestSpec, load_test_set, to_yaml
from scoring import fast
from scoring.holdout import evaluate_split, leave_one_out, loo_mean as mean_of_folds
from scoring.prune import TestInfo, classify, coverage, greedy_cover

ROOT = Path(__file__).resolve().parents[1]


def resolve(name: str, test_sets: Path) -> Path:
    p = Path(name)
    return p if p.exists() else test_sets / name


def load_candidates(paths: list[Path], ds: datasets.Dataset) -> tuple[list[TestSpec], dict[str, str], Counter]:
    contexts = load_contexts(ds.snapshot, list(ds.models), sample_rows=1)
    specs: list[TestSpec] = []
    origin: dict[str, str] = {}
    duplicates: Counter = Counter()
    for path in paths:
        for s in load_test_set(path, contexts, source=path.stem):
            if s.test_id in origin:          # identical test already present from an earlier set
                duplicates[path.stem] += 1
            else:
                origin[s.test_id] = path.stem
                specs.append(s)
    return specs, origin, duplicates


def test_set_hash(candidate_text: str) -> str:
    # Same hash on Windows and Linux (Airflow), so either can reuse the other's saved results.
    # "v2": results saved before the run lock existed may come from overlapping runs; ignore them.
    return hashlib.sha1(("v2\n" + candidate_text).encode("utf-8")).hexdigest()[:12]


def run_on(db: Path, run_name: str, log_dir: Path, ds: datasets.Dataset) -> dict[str, str]:
    """test_id -> status for one database."""
    run = run_dbt("test", db, run_name, log_dir, ds.models, dbt_args=ds.dbt_args())
    return {r.test_id: r.status for r in run.results if r.test_id}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="football", choices=list(datasets.DATASETS))
    p.add_argument("--test-set", nargs="+", required=True, help="test set files (manual baseline first)")
    p.add_argument("--mutants", type=Path, help="mutation manifest (default: the dataset's manifest.jsonl)")
    p.add_argument("--config", type=Path, help="held-out split (default: the dataset's scoring config)")
    p.add_argument("--workers", type=int, default=4, help="mutants scored in parallel")
    p.add_argument("--engine", choices=["fast", "dbt"], default="fast",
                   help="fast: compile once, run SQL in DuckDB (default); dbt: dbt test per mutant")
    p.add_argument("--name", help="output name (default: the test set names joined)")
    p.add_argument("--limit", type=int, help="score only the first N mutants (for a quick try)")
    args = p.parse_args()
    ds = datasets.get(args.dataset)
    args.mutants = args.mutants or ds.manifest
    args.config = args.config or ds.scoring_config
    models = list(ds.models)

    paths = [resolve(t, ds.test_sets) for t in args.test_set]
    missing = [str(x) for x in paths if not x.exists()]
    if missing:
        print(f"Missing test sets: {missing}")
        return 1
    if not args.mutants.exists():
        print(f"Missing {args.mutants}. Run: uv run python -m mutations.run --dataset {ds.name}")
        return 1
    name = args.name or "__".join(x.stem for x in paths)
    out = ds.report_dir("scoring") / name
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    specs, origin, duplicates = load_candidates(paths, ds)
    priority = {x.stem: i for i, x in enumerate(paths)}
    print(f"Candidate tests: {len(specs)} " +
          "(" + ", ".join(f"{k}: {v}" for k, v in Counter(origin.values()).items()) + ")")

    mutants = [json.loads(line) for line in args.mutants.read_text(encoding="utf-8").splitlines() if line.strip()]
    mutants = [m for m in mutants if m.get("status") == "ok" and datasets.resolve_file(m["file"]).exists()]
    if args.limit:
        mutants = mutants[: args.limit]
    mutant_ops = {m["mutation_id"]: m["operator"] for m in mutants}
    print(f"Mutants to score: {len(mutants)}")

    candidate_file = out / "candidates.yml"
    candidate_text = to_yaml(specs, models, "# All candidate tests for scoring.")
    candidate_file.write_text(candidate_text, encoding="utf-8", newline="\n")
    set_hash = test_set_hash(candidate_text)

    statuses: dict[str, dict[str, str]] = {}
    start = time.time()
    todo = []
    for m in mutants:
        cached = runs_dir / f"{m['mutation_id']}.json"
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("test_set_hash") == set_hash:
                statuses[m["mutation_id"]] = data["status"]
                continue
        todo.append(m)
    if len(todo) < len(mutants):
        print(f"Reusing saved results for {len(mutants) - len(todo)} mutants.")

    def save(m: dict, st: dict[str, str]) -> tuple[str, dict[str, str]]:
        (runs_dir / f"{m['mutation_id']}.json").write_text(
            json.dumps({"test_set_hash": set_hash, "engine": args.engine, "status": st}), encoding="utf-8")
        return m["mutation_id"], st

    def run_all(label: str, work) -> None:
        if not todo:
            return
        print(f"Running {label} on {len(todo)} mutants with {args.workers} workers ...")
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(work, m) for m in todo]
            step = 10 if args.engine == "dbt" else 50
            for i, f in enumerate(as_completed(futures), 1):
                mid, st = f.result()
                statuses[mid] = st
                if i % step == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)} ({time.time() - start:.0f}s)")

    if args.engine == "fast":
        # The test set only has to be swapped in while dbt compiles it.
        with active_test_set(candidate_file, ds.active_schema):
            compiled = fast.compile_tests(ds, f"{ds.name}_scoring_compile_{set_hash}", out / "logs")
        print(f"Compiled {len(compiled)} tests once; running their SQL directly in DuckDB.")
        clean_status = fast.run_tests(ds.snapshot, compiled)
        run_all("the compiled tests", lambda m: save(m, fast.run_tests(datasets.resolve_file(m["file"]), compiled)))
    else:
        with active_test_set(candidate_file, ds.active_schema):
            clean_copy = fresh_copy(ds.snapshot, f"{ds.name}_scoring_clean_{set_hash}")
            clean_status = run_on(clean_copy, f"{ds.name}_scoring_clean_{set_hash}", out / "logs", ds)
            run_all("dbt", lambda m: save(m, run_on(datasets.resolve_file(m["file"]),
                                                  f"{ds.name}_scoring_{m['mutation_id']}", out / "logs", ds)))
    not_run = [s.test_id for s in specs if s.test_id not in clean_status]
    if not_run:
        print(f"WARNING: {len(not_run)} tests did not run on clean data; they are treated as broken.")
    print(f"Scoring time: {time.time() - start:.0f}s")

    # ---------------------------------------------------------------- matrix
    tests = [TestInfo(s.test_id, origin[s.test_id], priority[origin[s.test_id]],
                      broken=clean_status.get(s.test_id) != "pass") for s in specs]
    catches: dict[str, set[str]] = defaultdict(set)
    for mid, st in statuses.items():
        for tid, status in st.items():
            if status in ("fail", "error"):
                catches[tid].add(mid)
    all_mutants = set(statuses)

    labels = classify(tests, catches, all_mutants)
    kept = greedy_cover(tests, catches, all_mutants)
    live = [t.test_id for t in tests if not t.broken]
    detectable = coverage(live, catches, all_mutants)
    undetected = sorted(all_mutants - detectable)

    # ---------------------------------------------------------------- held-out
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) if args.config.exists() else {}
    fixed = None
    if cfg.get("selection") and cfg.get("heldout"):
        fixed = evaluate_split(tests, catches, mutant_ops, set(cfg["selection"]), set(cfg["heldout"]))
    loo = leave_one_out(tests, catches, mutant_ops)
    loo_mean = mean_of_folds(loo)

    # ---------------------------------------------------------------- outputs
    spec_by_id = {s.test_id: s for s in specs}
    pruned_path = ds.test_sets / f"pruned_{name}.yml"
    pruned_path.write_text(to_yaml([spec_by_id[t] for t in kept], models,
                                   f"# Minimal test set chosen by scoring/run.py from: {', '.join(x.name for x in paths)}.\n"
                                   "# Do not edit by hand."), encoding="utf-8")

    with (out / "tests.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["test_id", "source", "model", "column", "type", "class", "kept",
                    "mutants_caught", "operators_caught"])
        for s in specs:
            ops = Counter(mutant_ops[m] for m in catches.get(s.test_id, set()))
            w.writerow([s.test_id, origin[s.test_id], s.model, s.column or "", s.type, labels[s.test_id],
                        s.test_id in kept, len(catches.get(s.test_id, set())),
                        ";".join(f"{k}:{v}" for k, v in sorted(ops.items()))])
    with (out / "matrix.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        order = sorted(all_mutants)
        w.writerow(["test_id", *order])
        for s in specs:
            w.writerow([s.test_id, *(int(m in catches.get(s.test_id, set())) for m in order)])

    per_source = {}
    for src in priority:
        mine = [t for t in tests if t.source == src]
        per_source[src] = {"tests": len(mine), **Counter(labels[t.test_id] for t in mine),
                           "kept": sum(t.test_id in kept for t in mine),
                           "identical_to_earlier_set": duplicates.get(src, 0)}
    per_operator = {}
    for op in sorted(set(mutant_ops.values())):
        ms = {m for m in all_mutants if mutant_ops[m] == op}
        per_operator[op] = {"mutants": len(ms), "caught_by_full_set": len(ms & detectable),
                            "caught_by_kept_set": len(coverage(kept, catches, ms))}

    summary = {
        "name": name, "dataset": ds.name, "test_sets": [x.name for x in paths], "candidate_tests": len(specs),
        "mutants": len(all_mutants), "mutants_caught": len(detectable), "mutants_never_caught": undetected,
        "classes": dict(Counter(labels.values())), "per_source": per_source, "per_operator": per_operator,
        "kept_tests": kept, "pruned_test_set": str(pruned_path.relative_to(ROOT)),
        "heldout_fixed": fixed, "heldout_leave_one_out": loo,
        "heldout_leave_one_out_mean_pct_of_full": loo_mean,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- print
    print(f"\nMutants caught by at least one test: {len(detectable)}/{len(all_mutants)}")
    print("\nPer operator (caught by all candidates / by the kept set):")
    for op, v in per_operator.items():
        print(f"  {op:18s} {v['caught_by_full_set']:3d}/{v['mutants']:3d}   kept {v['caught_by_kept_set']:3d}")
    print("\nTest classes:")
    for src, v in per_source.items():
        parts = ", ".join(f"{k} {v.get(k, 0)}" for k in ("useful", "redundant", "low_value", "broken"))
        dup = f" (+{v['identical_to_earlier_set']} identical to an earlier set, not counted)" \
            if v["identical_to_earlier_set"] else ""
        print(f"  {src:22s} {v['tests']:3d} tests: {parts}; kept {v['kept']}{dup}")
    print(f"\nMinimal set: {len(kept)} of {len(live)} working tests catch all {len(detectable)} detectable mutants.")
    print(f"Saved {pruned_path.relative_to(ROOT)}")

    def show(split: dict) -> str:
        return (f"kept {split['kept_tests']:2d} tests -> caught {split['heldout_caught_by_kept_set']}/"
                f"{split['heldout_mutants']} unseen mutants ({split['kept_pct_of_heldout']}%); "
                f"all candidates caught {split['heldout_caught_by_full_set']} ({split['full_pct_of_heldout']}%)")
    print("\nHeld-out evaluation:")
    if fixed:
        print(f"  fixed split, held out {', '.join(fixed['heldout_operators'])}:")
        print(f"    {show(fixed)}")
    print("  leave one operator out:")
    for s in loo:
        print(f"    {s['heldout_operators'][0]:18s} {show(s)}")
    if loo_mean is not None:
        print(f"  average over operators: the kept set catches {loo_mean}% of what the full set catches "
              f"on the held-out operator")

    llm_sources = [x.stem for x in paths if not x.stem.startswith("manual")]
    for src in llm_sources:
        v = per_source[src]
        total = v["tests"] + v["identical_to_earlier_set"]
        redundant = v.get("redundant", 0) + v["identical_to_earlier_set"]
        print(f"\n{src}: {total} generated tests -> useful {v.get('useful', 0)}, redundant {redundant} "
              f"(incl. {v['identical_to_earlier_set']} identical to manual), low-value {v.get('low_value', 0)}, "
              f"broken {v.get('broken', 0)}."
              + (" Paper (GPT-4.1-mini, 16 anomalies): 25 -> 9 / 4 / 12." if ds.name == "football" else ""))
    if fixed and fixed["heldout_mutants"]:
        print(f"Held-out headline: pruning on {', '.join(fixed['selection_operators'])} kept "
              f"{fixed['kept_tests']} of {len(live)} tests; on unseen operators they catch "
              f"{fixed['kept_pct_of_heldout']}% of mutants, against {fixed['full_pct_of_heldout']}% for all tests "
              f"({fixed['kept_pct_of_full_on_heldout']}% of what the full set catches).")
    print(f"\nDetails: {out.relative_to(ROOT)}/summary.json, tests.csv, matrix.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
