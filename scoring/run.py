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

Run from the data_eng folder:
  uv run python -m scoring.run --test-set manual_baseline.yml llm_gemini.yml
  uv run python -m scoring.run --test-set manual_baseline.yml llm_groq.yml --workers 4
Outputs: reports/scoring/<name>/ and dbt_project/test_sets/pruned_<name>.yml
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

from common.dbt_runner import CLEAN_DB, DBT_DIR, active_test_set, fresh_copy, run_dbt
from llm_tests.context import load_contexts
from llm_tests.validate import TestSpec, load_test_set, to_yaml
from scoring.holdout import evaluate_split, leave_one_out
from scoring.prune import TestInfo, classify, coverage, greedy_cover

ROOT = Path(__file__).resolve().parents[1]
TEST_SETS = DBT_DIR / "test_sets"
MODELS = ["dim_teams", "fct_matches", "fct_training_dataset"]


def resolve(name: str) -> Path:
    p = Path(name)
    return p if p.exists() else TEST_SETS / name


def load_candidates(paths: list[Path]) -> tuple[list[TestSpec], dict[str, str], Counter]:
    contexts = load_contexts(CLEAN_DB, MODELS, sample_rows=1)
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


def run_on(db: Path, run_name: str, log_dir: Path) -> dict[str, str]:
    """test_id -> status for one database."""
    run = run_dbt("test", db, run_name, log_dir, tuple(MODELS))
    return {r.test_id: r.status for r in run.results if r.test_id}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--test-set", nargs="+", required=True, help="test set files (manual baseline first)")
    p.add_argument("--mutants", type=Path, default=ROOT / "data" / "mutants" / "manifest.jsonl")
    p.add_argument("--config", type=Path, default=ROOT / "scoring" / "config.yml")
    p.add_argument("--workers", type=int, default=4, help="dbt runs in parallel")
    p.add_argument("--name", help="output name (default: the test set names joined)")
    p.add_argument("--limit", type=int, help="score only the first N mutants (for a quick try)")
    args = p.parse_args()

    paths = [resolve(t) for t in args.test_set]
    missing = [str(x) for x in paths if not x.exists()]
    if missing:
        print(f"Missing test sets: {missing}")
        return 1
    if not args.mutants.exists():
        print(f"Missing {args.mutants}. Run: uv run python -m mutations.run")
        return 1
    name = args.name or "__".join(x.stem for x in paths)
    out = ROOT / "reports" / "scoring" / name
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    specs, origin, duplicates = load_candidates(paths)
    priority = {x.stem: i for i, x in enumerate(paths)}
    print(f"Candidate tests: {len(specs)} " +
          "(" + ", ".join(f"{k}: {v}" for k, v in Counter(origin.values()).items()) + ")")

    mutants = [json.loads(line) for line in args.mutants.read_text(encoding="utf-8").splitlines() if line.strip()]
    mutants = [m for m in mutants if m.get("status") == "ok" and (ROOT / m["file"]).exists()]
    if args.limit:
        mutants = mutants[: args.limit]
    mutant_ops = {m["mutation_id"]: m["operator"] for m in mutants}
    print(f"Mutants to score: {len(mutants)}")

    candidate_file = out / "candidates.yml"
    candidate_file.write_text(to_yaml(specs, MODELS, "# All candidate tests for scoring."), encoding="utf-8")
    set_hash = hashlib.sha1(candidate_file.read_bytes()).hexdigest()[:12]

    statuses: dict[str, dict[str, str]] = {}
    start = time.time()
    with active_test_set(candidate_file):
        clean_copy = fresh_copy(CLEAN_DB, f"scoring_clean_{set_hash}")
        clean_status = run_on(clean_copy, f"scoring_clean_{set_hash}", out / "logs")
        not_run = [s.test_id for s in specs if s.test_id not in clean_status]
        if not_run:
            print(f"WARNING: {len(not_run)} tests did not run on clean data; they are treated as broken.")

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

        def work(m: dict) -> tuple[str, dict[str, str]]:
            st = run_on(ROOT / m["file"], f"scoring_{m['mutation_id']}", out / "logs")
            (runs_dir / f"{m['mutation_id']}.json").write_text(
                json.dumps({"test_set_hash": set_hash, "status": st}), encoding="utf-8")
            return m["mutation_id"], st

        if todo:
            print(f"Running dbt on {len(todo)} mutants with {args.workers} workers ...")
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                futures = [pool.submit(work, m) for m in todo]
                for i, f in enumerate(as_completed(futures), 1):
                    mid, st = f.result()
                    statuses[mid] = st
                    if i % 10 == 0 or i == len(todo):
                        print(f"  {i}/{len(todo)} ({time.time() - start:.0f}s)")

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

    # ---------------------------------------------------------------- outputs
    spec_by_id = {s.test_id: s for s in specs}
    pruned_path = TEST_SETS / f"pruned_{name}.yml"
    pruned_path.write_text(to_yaml([spec_by_id[t] for t in kept], MODELS,
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
        "name": name, "test_sets": [x.name for x in paths], "candidate_tests": len(specs),
        "mutants": len(all_mutants), "mutants_caught": len(detectable), "mutants_never_caught": undetected,
        "classes": dict(Counter(labels.values())), "per_source": per_source, "per_operator": per_operator,
        "kept_tests": kept, "pruned_test_set": str(pruned_path.relative_to(ROOT)),
        "heldout_fixed": fixed, "heldout_leave_one_out": loo,
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

    llm_sources = [x.stem for x in paths if not x.stem.startswith("manual")]
    for src in llm_sources:
        v = per_source[src]
        total = v["tests"] + v["identical_to_earlier_set"]
        redundant = v.get("redundant", 0) + v["identical_to_earlier_set"]
        print(f"\n{src}: {total} generated tests -> useful {v.get('useful', 0)}, redundant {redundant} "
              f"(incl. {v['identical_to_earlier_set']} identical to manual), low-value {v.get('low_value', 0)}, "
              f"broken {v.get('broken', 0)}. Paper (GPT-4.1-mini, 16 anomalies): 25 -> 9 / 4 / 12.")
    if fixed and fixed["heldout_mutants"]:
        print(f"Held-out headline: pruning on {', '.join(fixed['selection_operators'])} kept "
              f"{fixed['kept_tests']} of {len(live)} tests; on unseen operators they catch "
              f"{fixed['kept_pct_of_heldout']}% of mutants, against {fixed['full_pct_of_heldout']}% for all tests "
              f"({fixed['kept_pct_of_full_on_heldout']}% of what the full set catches).")
    print(f"\nDetails: {out.relative_to(ROOT)}/summary.json, tests.csv, matrix.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
