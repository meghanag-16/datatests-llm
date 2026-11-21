"""Reproduce the paper's anomaly-injection experiment (manual-only 7/16).

Adapted from the paper authors' experiments/run_multibatch_anomaly_experiment.py
(github.com/igargouri10/football-analytics-platform, MIT licence). The 16 anomalies,
the 3 batches and the anomaly-to-test mapping are theirs, unchanged.

For every batch (A, B, C) and every test set (condition):
  1. copy the clean snapshot data/snapshots/dbt.clean.duckdb to a temporary database
  2. make that test set the active models/marts/schema.yml
  3. inject the batch's anomalies into the temporary database
  4. run dbt test on it and record which tests failed
  5. count an anomaly as detected if one of its mapped tests failed
At the end models/marts/schema.yml is put back exactly as it was.

Conditions (test sets in dbt_project/test_sets/):
  manual_only      manual_baseline.yml   paper: 7/16
  manual_expanded  manual_expanded.yml   paper: 16/16
  manual_plus_llm  authors_llm.yml       paper: 16/16 (the authors' GPT-4.1-mini tests)

Run from the data_eng folder:
    uv run python experiments/run_anomaly_experiment.py
    uv run python experiments/run_anomaly_experiment.py --conditions manual_only
    uv run python experiments/run_anomaly_experiment.py --test-set manual_plus_llm_gemini.yml
Results go to reports/anomaly_results/.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from data_eng
from common.dbt_runner import protected_schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt_project"
TEST_SETS = DBT_DIR / "test_sets"
ACTIVE_SCHEMA = DBT_DIR / "models" / "marts" / "schema.yml"
CLEAN_DB = ROOT / "data" / "snapshots" / "dbt.clean.duckdb"
TEMP_DB_DIR = ROOT / "data" / "experiment_dbs"
OUTPUT_DIR = ROOT / "reports" / "anomaly_results"

CONDITIONS = {
    "manual_only": TEST_SETS / "manual_baseline.yml",
    "manual_expanded": TEST_SETS / "manual_expanded.yml",
    "manual_plus_llm": TEST_SETS / "authors_llm.yml",
}

# Detected counts reported in the paper (section 4.3) and in the authors' results files.
PAPER_RESULTS = {
    "manual_only": {"batch_A_key_integrity": 4, "batch_B_semantic_domain": 0, "batch_C_mixed_full_stack": 3},
    "manual_expanded": {"batch_A_key_integrity": 4, "batch_B_semantic_domain": 6, "batch_C_mixed_full_stack": 6},
    "manual_plus_llm": {"batch_A_key_integrity": 4, "batch_B_semantic_domain": 6, "batch_C_mixed_full_stack": 6},
}

# Which dbt tests count as catching each anomaly (authors' mapping, unchanged).
# A failed test matches if its id starts with or contains one of these names.
ANOMALY_DETECTORS = {
    "null_dim_teams_team_id": ["not_null_dim_teams_team_id"],
    "duplicate_dim_teams_team_id": ["unique_dim_teams_team_id"],
    "null_dim_teams_team_name": ["not_null_dim_teams_team_name"],
    "null_fct_matches_match_id": [
        "not_null_fct_matches_match_id",
        "not_null_fct_training_dataset_match_id",
    ],
    "duplicate_fct_matches_match_id": [
        "unique_fct_matches_match_id",
        "unique_fct_training_dataset_match_id",
    ],
    "null_fct_matches_match_status": ["not_null_fct_matches_match_status"],
    "invalid_fct_matches_match_status": ["accepted_values_fct_matches_match_status"],
    "null_fct_matches_home_team_id": [
        "not_null_fct_matches_home_team_id",
        "not_null_fct_training_dataset_home_team_id",
    ],
    "null_fct_matches_away_team_id": [
        "not_null_fct_matches_away_team_id",
        "not_null_fct_training_dataset_away_team_id",
    ],
    "null_fct_matches_match_date": [
        "not_null_fct_matches_match_date",
        "not_null_fct_training_dataset_match_date",
    ],
}

BATCHES = {
    "batch_A_key_integrity": "Core key-integrity anomalies in dimension and fact tables.",
    "batch_B_semantic_domain": "Semantic/domain and completeness anomalies primarily targeted by the expanded test layer.",
    "batch_C_mixed_full_stack": "Mixed anomalies spanning dimension, fact, and downstream propagated effects.",
}

ALLOWED_STATUSES = ("Match Finished", "Match Postponed", "Match Cancelled", "Match Scheduled", "In Progress")


# ---------------------------------------------------------------- injection (authors' SQL)

def inject_batch(db_path: Path, batch_id: str) -> list[dict]:
    con = duckdb.connect(str(db_path))
    manifest: list[dict] = []
    add = lambda aid, target: manifest.append({"id": aid, "target": target})
    try:
        teams = con.execute("SELECT team_id, team_name FROM main.dim_teams ORDER BY team_id").fetchall()
        matches = [r[0] for r in con.execute("SELECT match_id FROM main.fct_matches ORDER BY match_id").fetchall()]

        if batch_id == "batch_A_key_integrity":
            t1, t2 = teams[0], teams[1]
            m1, m2 = matches[0], matches[1]
            con.execute("UPDATE main.dim_teams SET team_id = NULL WHERE team_id = ?", [t2[0]])
            add("null_dim_teams_team_id", f"dim_teams.team_id={t2[0]}")
            con.execute("INSERT INTO main.dim_teams (team_id, team_name) VALUES (?, ?)", [t1[0], f"{t1[1]}_DUP"])
            add("duplicate_dim_teams_team_id", f"dim_teams.team_id={t1[0]}")
            con.execute("UPDATE main.fct_matches SET match_id = NULL WHERE match_id = ?", [m2])
            add("null_fct_matches_match_id", f"fct_matches.match_id={m2}")
            con.execute("INSERT INTO main.fct_matches SELECT * FROM main.fct_matches WHERE match_id = ?", [m1])
            add("duplicate_fct_matches_match_id", f"fct_matches.match_id={m1}")

        elif batch_id == "batch_B_semantic_domain":
            t3 = teams[3]
            m3, m4, m5, m6, m7, _m8 = matches[10:16]
            con.execute("UPDATE main.dim_teams SET team_name = NULL WHERE team_id = ?", [t3[0]])
            add("null_dim_teams_team_name", f"dim_teams.team_id={t3[0]}")
            con.execute("UPDATE main.fct_matches SET match_status = NULL WHERE match_id = ?", [m3])
            add("null_fct_matches_match_status", f"fct_matches.match_id={m3}")
            con.execute("UPDATE main.fct_matches SET match_status = 'BROKEN_STATUS' WHERE match_id = ?", [m4])
            add("invalid_fct_matches_match_status", f"fct_matches.match_id={m4}")
            con.execute("UPDATE main.fct_matches SET home_team_id = NULL WHERE match_id = ?", [m5])
            add("null_fct_matches_home_team_id", f"fct_matches.match_id={m5}")
            con.execute("UPDATE main.fct_matches SET away_team_id = NULL WHERE match_id = ?", [m6])
            add("null_fct_matches_away_team_id", f"fct_matches.match_id={m6}")
            con.execute("UPDATE main.fct_matches SET match_date = NULL WHERE match_id = ?", [m7])
            add("null_fct_matches_match_date", f"fct_matches.match_id={m7}")

        elif batch_id == "batch_C_mixed_full_stack":
            t4, t5 = teams[4], teams[5]
            m9, m10, m11, _m12 = matches[20:24]
            con.execute("UPDATE main.dim_teams SET team_id = NULL WHERE team_id = ?", [t5[0]])
            add("null_dim_teams_team_id", f"dim_teams.team_id={t5[0]}")
            con.execute("INSERT INTO main.dim_teams (team_id, team_name) VALUES (?, ?)", [t4[0], f"{t4[1]}_DUP2"])
            add("duplicate_dim_teams_team_id", f"dim_teams.team_id={t4[0]}")
            con.execute("UPDATE main.dim_teams SET team_name = NULL WHERE team_id = ?", [t4[0]])
            add("null_dim_teams_team_name", f"dim_teams.team_id={t4[0]}")
            con.execute("INSERT INTO main.fct_matches SELECT * FROM main.fct_matches WHERE match_id = ?", [m9])
            add("duplicate_fct_matches_match_id", f"fct_matches.match_id={m9}")
            con.execute("UPDATE main.fct_matches SET home_team_id = NULL WHERE match_id = ?", [m10])
            add("null_fct_matches_home_team_id", f"fct_matches.match_id={m10}")
            con.execute("UPDATE main.fct_matches SET match_date = NULL WHERE match_id = ?", [m11])
            add("null_fct_matches_match_date", f"fct_matches.match_id={m11}")
        else:
            raise ValueError(f"Unknown batch: {batch_id}")
    finally:
        con.close()
    return manifest


def verify_injected(db_path: Path, manifest: list[dict]) -> dict[str, bool]:
    """Confirm each anomaly really is present in the damaged database."""
    allowed = ", ".join(f"'{s}'" for s in ALLOWED_STATUSES)
    queries = {
        "null_dim_teams_team_id": "SELECT COUNT(*) FROM main.dim_teams WHERE team_id IS NULL",
        "duplicate_dim_teams_team_id": "SELECT COUNT(*) FROM (SELECT team_id FROM main.dim_teams "
                                       "WHERE team_id IS NOT NULL GROUP BY team_id HAVING COUNT(*) > 1)",
        "null_dim_teams_team_name": "SELECT COUNT(*) FROM main.dim_teams WHERE team_name IS NULL",
        "null_fct_matches_match_id": "SELECT COUNT(*) FROM main.fct_matches WHERE match_id IS NULL",
        "duplicate_fct_matches_match_id": "SELECT COUNT(*) FROM (SELECT match_id FROM main.fct_matches "
                                          "WHERE match_id IS NOT NULL GROUP BY match_id HAVING COUNT(*) > 1)",
        "null_fct_matches_match_status": "SELECT COUNT(*) FROM main.fct_matches WHERE match_status IS NULL",
        "invalid_fct_matches_match_status": "SELECT COUNT(*) FROM main.fct_matches WHERE match_status IS NOT NULL "
                                            f"AND match_status NOT IN ({allowed})",
        "null_fct_matches_home_team_id": "SELECT COUNT(*) FROM main.fct_matches WHERE home_team_id IS NULL",
        "null_fct_matches_away_team_id": "SELECT COUNT(*) FROM main.fct_matches WHERE away_team_id IS NULL",
        "null_fct_matches_match_date": "SELECT COUNT(*) FROM main.fct_matches WHERE match_date IS NULL",
    }
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {m["id"]: con.execute(queries[m["id"]]).fetchone()[0] > 0 for m in manifest}
    finally:
        con.close()


# ---------------------------------------------------------------- dbt

def dbt_command() -> list[str]:
    exe = shutil.which("dbt")
    if not exe:
        sys.exit("Could not find dbt. Run this script with: uv run python experiments/run_anomaly_experiment.py")
    return [exe]


def run_dbt_test(db_path: Path, run_name: str) -> dict:
    target_dir = DBT_DIR / "target" / "anomaly_runs" / run_name
    shutil.rmtree(target_dir, ignore_errors=True)  # only our own dbt output folder for this run
    cmd = dbt_command() + [
        "test",
        "--project-dir", str(DBT_DIR),
        "--profiles-dir", str(DBT_DIR),
        "--target-path", str(target_dir),
        "--select", "dim_teams", "fct_matches", "fct_training_dataset",
        "--no-partial-parse",
    ]
    env = os.environ.copy()
    env["DUCKDB_PATH"] = db_path.as_posix()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)

    log_path = OUTPUT_DIR / f"{run_name}_dbt_test.log"
    log_path.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")

    results_path = target_dir / "run_results.json"
    if not results_path.exists():
        raise RuntimeError(f"dbt did not produce results for {run_name}. See {log_path}")
    results = json.loads(results_path.read_text(encoding="utf-8")).get("results", [])
    failed = [r["unique_id"] for r in results if r.get("status", "").lower() in {"fail", "error"}]
    errored = [r["unique_id"] for r in results if r.get("status", "").lower() == "error"]
    return {"returncode": proc.returncode, "tests_run": len(results), "failed_tests": failed,
            "errored_tests": errored, "log_file": str(log_path.relative_to(ROOT))}


def detected(detectors: list[str], failed: list[str]) -> list[str]:
    hits = []
    for f in failed:
        name = f.split(".")[2] if f.count(".") >= 2 else f  # test.<project>.<name>.<hash>
        if any(name.startswith(d) or d in f for d in detectors):
            hits.append(name)
    return hits


# ---------------------------------------------------------------- main

def run_one(batch_id: str, condition: str, schema_source: Path) -> dict:
    run_name = f"{batch_id}__{condition}"
    temp_db = TEMP_DB_DIR / f"{run_name}.duckdb"
    shutil.copyfile(CLEAN_DB, temp_db)  # overwrites the previous temp copy, never the clean snapshot
    shutil.copyfile(schema_source, ACTIVE_SCHEMA)

    manifest = inject_batch(temp_db, batch_id)
    verified = verify_injected(temp_db, manifest)
    run = run_dbt_test(temp_db, run_name)

    detection = []
    for item in manifest:
        hits = detected(ANOMALY_DETECTORS[item["id"]], run["failed_tests"])
        detection.append({"anomaly_id": item["id"], "target": item["target"],
                          "injected": verified[item["id"]], "detected": bool(hits), "caught_by": hits})
    n_det = sum(d["detected"] for d in detection)
    result = {"batch_id": batch_id, "condition": condition, "test_set": schema_source.name,
              "detected_count": n_det, "total_anomalies": len(detection),
              "paper_detected_count": PAPER_RESULTS[condition].get(batch_id),
              "detection": detection, **run}
    (OUTPUT_DIR / f"{run_name}_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    paper_n = PAPER_RESULTS[condition].get(batch_id)
    paper_txt = f" (paper {paper_n}/{len(detection)})" if paper_n is not None else ""
    flag = "   <-- differs from paper" if paper_n is not None and n_det != paper_n else ""
    print(f"  {batch_id:26s} {condition:16s} detected {n_det}/{len(detection)}{paper_txt}{flag}")
    if not all(verified.values()):
        print(f"    WARNING: some anomalies were not injected: {verified}")
    if run["errored_tests"]:
        print(f"    WARNING: {len(run['errored_tests'])} tests errored (not just failed). See {run['log_file']}")
    return result


def main() -> int:
    global OUTPUT_DIR
    p = argparse.ArgumentParser()
    p.add_argument("--conditions", nargs="+", choices=list(CONDITIONS), default=list(CONDITIONS))
    p.add_argument("--test-set", nargs="+", default=[], metavar="FILE",
                   help="extra test set(s) from dbt_project/test_sets to run, e.g. manual_plus_llm_gemini.yml")
    p.add_argument("--out", type=Path, default=OUTPUT_DIR,
                   help="results folder (default: reports/anomaly_results)")
    args = p.parse_args()
    OUTPUT_DIR = args.out if args.out.is_absolute() else ROOT / args.out
    for f in args.test_set:
        path = Path(f) if Path(f).is_absolute() or Path(f).exists() else TEST_SETS / f
        name = path.stem
        CONDITIONS[name] = path
        PAPER_RESULTS[name] = {}
        args.conditions.append(name)

    if not CLEAN_DB.exists():
        print(f"Missing {CLEAN_DB}. Build the models and run scripts/snapshot_clean.py first.")
        return 1
    missing = [str(CONDITIONS[c]) for c in args.conditions if not CONDITIONS[c].exists()]
    if missing:
        print(f"Missing test sets: {missing}")
        return 1
    TEMP_DB_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    # protected_schema keeps a backup while test sets are swapped in and restores the file
    # afterwards, also after a run that was killed before it could clean up.
    with protected_schema(ACTIVE_SCHEMA) as original_schema:
        (OUTPUT_DIR / "schema.yml.before_experiment").write_bytes(original_schema)
        print("Running batches (each run starts from a fresh copy of the clean snapshot):")
        for batch_id in BATCHES:
            for condition in args.conditions:
                results.append(run_one(batch_id, condition, CONDITIONS[condition]))
    print(f"Restored {ACTIVE_SCHEMA.relative_to(ROOT)}")

    summary = {}
    print("\nTotals:")
    for condition in args.conditions:
        rows = [r for r in results if r["condition"] == condition]
        det = sum(r["detected_count"] for r in rows)
        tot = sum(r["total_anomalies"] for r in rows)
        paper = sum(PAPER_RESULTS[condition].values()) if PAPER_RESULTS[condition] else None
        summary[condition] = {"detected": det, "total": tot, "paper_detected": paper,
                              "per_batch": {r["batch_id"]: r["detected_count"] for r in rows}}
        if paper is None:
            print(f"  {condition:16s} {det}/{tot}")
        else:
            match = "matches the paper" if det == paper else "DIFFERS from the paper"
            print(f"  {condition:16s} {det}/{tot}  (paper {paper}/16)  {match}")

    if "manual_only" in summary:
        base = summary["manual_only"]["detected"]
        for c in [c for c in args.conditions if c != "manual_only"]:
            if c in summary and base:
                gain = (summary[c]["detected"] - base) / base * 100
                summary[c]["relative_improvement_pct"] = round(gain, 2)
                paper_txt = " (paper: +128.57%)" if c in ("manual_expanded", "manual_plus_llm") else ""
                print(f"  {c} vs manual_only: {gain:+.2f}%{paper_txt}")

    (OUTPUT_DIR / "final_summary.json").write_text(
        json.dumps({"batches": BATCHES, "summary": summary, "runs": results}, indent=2), encoding="utf-8")
    with (OUTPUT_DIR / "detections.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["batch_id", "condition", "anomaly_id", "target", "injected", "detected", "caught_by"])
        for r in results:
            for d in r["detection"]:
                w.writerow([r["batch_id"], r["condition"], d["anomaly_id"], d["target"],
                            d["injected"], d["detected"], ";".join(d["caught_by"])])
    print(f"\nSaved {OUTPUT_DIR.relative_to(ROOT)}/final_summary.json and detections.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
