"""Stability check, following the protocol described in the paper (five repeated trials).

frozen  The generated test set is kept fixed and the anomaly experiment is run five times.
        This shows whether the experiment itself gives the same answer every time.
fresh   Tests are generated again from scratch for every trial (a new LLM call per model),
        and each new set goes through the same anomaly experiment. This shows how much the
        LLM output, and the detection result, change from one generation to the next.

For fresh trials the script also compares the generated test sets with each other
(how many tests each trial shares with the first one) so that run-to-run variation in
what the LLM writes is visible, not only variation in the final score.

Run from the data_eng folder:
  uv run python experiments/run_stability.py --provider gemini
  uv run python experiments/run_stability.py --provider groq --trials 5
  uv run python experiments/run_stability.py --provider gemini --mode fresh
Results: reports/stability/<provider>/stability.json and stability.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_SETS = ROOT / "dbt_project" / "test_sets"
EXPERIMENT_SUMMARY = ROOT / "reports" / "anomaly_results" / "final_summary.json"
PAPER = {"manual_only": 7, "manual_expanded": 16, "manual_plus_llm": 16}


def run(cmd: list[str]) -> None:
    print("  $ " + " ".join(cmd[1:]), flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-3000:], proc.stderr[-3000:], sep="\n")
        raise SystemExit(f"Command failed: {' '.join(cmd)}")


def anomaly_run(llm_set: Path) -> dict[str, dict]:
    run([sys.executable, "experiments/run_anomaly_experiment.py",
         "--conditions", "manual_only", "manual_expanded", "--test-set", str(llm_set)])
    summary = json.loads(EXPERIMENT_SUMMARY.read_text(encoding="utf-8"))["summary"]
    return {("manual_plus_llm" if k == llm_set.stem else k): v for k, v in summary.items()}


def test_ids(path: Path) -> set[str]:
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("test_id:"):
            ids.add(line.split(":", 1)[1].strip())
    return ids


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", required=True, help="gemini, groq, openrouter, ...")
    p.add_argument("--trials", type=int, default=5)
    p.add_argument("--mode", choices=["both", "frozen", "fresh"], default="both")
    args = p.parse_args()

    out = ROOT / "reports" / "stability" / args.provider
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    if args.mode in ("both", "frozen"):
        frozen_set = TEST_SETS / f"manual_plus_llm_{args.provider}.yml"
        if not frozen_set.exists():
            print(f"Missing {frozen_set}. Run: uv run python -m llm_tests.generate --provider {args.provider}")
            return 1
        print(f"\nFrozen test set ({frozen_set.name}), {args.trials} runs:")
        for t in range(1, args.trials + 1):
            res = anomaly_run(frozen_set)
            rows.append({"mode": "frozen", "trial": t, "test_set": frozen_set.name,
                         **{f"{k}_detected": v["detected"] for k, v in res.items()}})
            print(f"  trial {t}: " + ", ".join(f"{k} {v['detected']}/16" for k, v in res.items()))

    if args.mode in ("both", "fresh"):
        print(f"\nFresh generation with {args.provider}, {args.trials} trials:")
        first_ids: set[str] | None = None
        for t in range(1, args.trials + 1):
            name = f"llm_{args.provider}_trial{t}"
            run([sys.executable, "-m", "llm_tests.generate", "--provider", args.provider,
                 "--trial", str(t), "--name", name])
            report = json.loads((ROOT / "reports" / "generation" / name / "report.json").read_text(encoding="utf-8"))
            ids = test_ids(TEST_SETS / f"{name}.yml")
            first_ids = ids if first_ids is None else first_ids
            shared = len(ids & first_ids)
            res = anomaly_run(TEST_SETS / f"manual_plus_{name}.yml")
            rows.append({"mode": "fresh", "trial": t, "test_set": f"manual_plus_{name}.yml",
                         "generated_tests": report["counts"]["kept"],
                         "rejected_tests": report["counts"]["rejected"],
                         "shared_with_trial1": shared,
                         **{f"{k}_detected": v["detected"] for k, v in res.items()}})
            print(f"  trial {t}: {report['counts']['kept']} tests kept "
                  f"({shared} identical to trial 1), "
                  + ", ".join(f"{k} {v['detected']}/16" for k, v in res.items()))

    (out / "stability.json").write_text(json.dumps({"provider": args.provider, "paper": PAPER, "trials": rows},
                                                   indent=2), encoding="utf-8")
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("mode", "trial", "test_set"), k))
    with (out / "stability.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    print("\nSummary (paper: manual_only 7/16, manual_expanded 16/16, manual_plus_llm 16/16 in all 5 trials):")
    for mode in ("frozen", "fresh"):
        mine = [r for r in rows if r["mode"] == mode]
        if not mine:
            continue
        for cond in ("manual_only", "manual_expanded", "manual_plus_llm"):
            vals = [r.get(f"{cond}_detected") for r in mine]
            same = "stable" if len(set(vals)) == 1 else "VARIES"
            print(f"  {mode:6s} {cond:16s} {vals}  {same}")
        if mode == "fresh":
            print(f"  fresh  tests kept per trial  {[r['generated_tests'] for r in mine]}")
            print(f"  fresh  shared with trial 1   {[r['shared_with_trial1'] for r in mine]}")
    print(f"\nSaved {out.relative_to(ROOT)}/stability.json and stability.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
