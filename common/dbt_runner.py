"""Run dbt test against a copy of a DuckDB database with a chosen test set.

A test set is a dbt properties YAML file (models + data tests). dbt reads tests from
dbt_project/models/marts/schema.yml, so the chosen set is swapped in for the run and the
original file is always put back afterwards.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt_project"
ACTIVE_SCHEMA = DBT_DIR / "models" / "marts" / "schema.yml"
CLEAN_DB = ROOT / "data" / "snapshots" / "dbt.clean.duckdb"
WORK_DB_DIR = ROOT / "data" / "experiment_dbs"
DEFAULT_MODELS = ("dim_teams", "fct_matches", "fct_training_dataset")


@dataclass
class TestResult:
    unique_id: str          # dbt id, e.g. test.data_eng.not_null_fct_matches_match_id.abc123
    name: str               # dbt test name
    status: str             # pass | fail | error | skipped | warn
    failures: int | None
    test_id: str | None     # stable id from config.meta.test_id, when the test set sets one
    message: str = ""


@dataclass
class RunResult:
    returncode: int
    log_file: Path
    results: list[TestResult] = field(default_factory=list)

    @property
    def failed(self) -> list[TestResult]:
        return [r for r in self.results if r.status in {"fail", "error"}]


def dbt_executable() -> str:
    exe = shutil.which("dbt")
    if not exe:
        sys.exit("dbt not found. Run commands with 'uv run ...' from the data_eng folder.")
    return exe


@contextmanager
def active_test_set(test_set: Path):
    """Temporarily make `test_set` the active marts/schema.yml."""
    original = ACTIVE_SCHEMA.read_bytes()
    try:
        shutil.copyfile(test_set, ACTIVE_SCHEMA)
        yield
    finally:
        ACTIVE_SCHEMA.write_bytes(original)


def fresh_copy(source: Path, name: str) -> Path:
    """Copy a database to data/experiment_dbs/<name>.duckdb (overwriting an older copy)."""
    WORK_DB_DIR.mkdir(parents=True, exist_ok=True)
    dest = WORK_DB_DIR / f"{name}.duckdb"
    shutil.copyfile(source, dest)
    return dest


def run_dbt(command: str, db_path: Path, run_name: str, log_dir: Path,
            models: tuple[str, ...] = DEFAULT_MODELS, extra: list[str] | None = None) -> RunResult:
    """Run `dbt <command>` (test or parse) on db_path. The caller sets the active test set."""
    target_dir = DBT_DIR / "target" / "runs" / run_name
    started = time.time()
    cmd = [dbt_executable(), command, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
           "--target-path", str(target_dir), "--no-partial-parse"]
    if command == "test":
        cmd += ["--select", *models]
    cmd += extra or []
    env = os.environ.copy()
    env["DUCKDB_PATH"] = db_path.as_posix()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{run_name}_dbt_{command}.log"
    log_file.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    run = RunResult(returncode=proc.returncode, log_file=log_file)
    if command != "test":
        return run

    results_path = target_dir / "run_results.json"
    manifest_path = target_dir / "manifest.json"
    if not results_path.exists() or results_path.stat().st_mtime < started - 1:
        raise RuntimeError(f"dbt produced no results for {run_name}; see {log_file}")
    meta = {}
    if manifest_path.exists():
        nodes = json.loads(manifest_path.read_text(encoding="utf-8")).get("nodes", {})
        meta = {uid: (n.get("config", {}).get("meta") or n.get("meta") or {}) for uid, n in nodes.items()}
    for r in json.loads(results_path.read_text(encoding="utf-8")).get("results", []):
        uid = r.get("unique_id", "")
        run.results.append(TestResult(
            unique_id=uid,
            name=uid.split(".")[2] if uid.count(".") >= 2 else uid,
            status=(r.get("status") or "").lower(),
            failures=r.get("failures"),
            test_id=meta.get(uid, {}).get("test_id"),
            message=(r.get("message") or "")[:300],
        ))
    return run
