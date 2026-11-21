"""Run dbt test against a copy of a DuckDB database with a chosen test set.

A test set is a dbt properties YAML file (models + data tests). dbt reads a dataset's tests
from its active properties file (football: dbt_project/models/marts/schema.yml), so the chosen
set is swapped in for the run and the original file is always put back afterwards. A backup is
written next to it during the swap; if a run is killed before it can restore the file, the next
run restores it from that backup first.

Only one program at a time may swap a dataset's test sets: two runs would overwrite each
other's file and test the wrong tests. A lock file next to schema.yml is held for the whole
swap and refreshed every few seconds; a second run stops with a message instead of starting.
A lock that has not been refreshed for LOCK_STALE_SECONDS belongs to a killed run and is taken over.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
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
DBT_TIMEOUT_SECONDS = int(os.environ.get("DBT_TIMEOUT_SECONDS", "900"))  # one dbt call
LOCK_STALE_SECONDS = 120
LOCK_REFRESH_SECONDS = 10


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
def schema_lock(schema: Path):
    """Hold <schema>.lock while a test set is swapped in; refuse if another live run holds it."""
    lock = schema.with_name(schema.name + ".lock")
    owner = f"{socket.gethostname()} pid {os.getpid()} since {time.strftime('%Y-%m-%d %H:%M:%S')}"
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                held_by = lock.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                continue  # released in the meantime; try again
            if age < LOCK_STALE_SECONDS:
                sys.exit(f"Another run is using {schema.name} right now ({held_by}).\n"
                         "Wait for it to finish (or stop it) before starting this one: two runs at the "
                         "same time overwrite each other's test sets.")
            print(f"Taking over a lock left by a stopped run ({held_by}).")
            lock.unlink(missing_ok=True)
    else:
        sys.exit(f"Could not take {lock.name}; try again.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(owner)

    stop = threading.Event()

    def refresh() -> None:
        while not stop.wait(LOCK_REFRESH_SECONDS):
            try:
                os.utime(lock)
            except OSError:
                pass

    beat = threading.Thread(target=refresh, daemon=True)
    beat.start()
    # Airflow stops a task (cleared, marked failed, timed out) with SIGTERM. Turn it into a normal
    # exit so the original schema.yml is put back and the lock is released.
    old_handler = None
    if threading.current_thread() is threading.main_thread():
        def on_term(signum, frame):
            raise SystemExit(f"Stopped by signal {signum}; restoring {schema.name}.")
        old_handler = signal.signal(signal.SIGTERM, on_term)
    try:
        yield
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGTERM, old_handler)
        stop.set()
        beat.join(timeout=5)
        lock.unlink(missing_ok=True)


@contextmanager
def protected_schema(schema: Path = ACTIVE_SCHEMA):
    with schema_lock(schema):
        with _swap_guard(schema) as original:
            yield original


@contextmanager
def _swap_guard(schema: Path):
    """Keep `schema` safe while its content is replaced, and put it back afterwards."""
    backup = schema.with_name(schema.name + ".bak")
    if backup.exists():
        print(f"Restoring {schema.name} from {backup.name} (left by an interrupted run).")
        schema.write_bytes(backup.read_bytes())
    original = schema.read_bytes()
    backup.write_bytes(original)
    try:
        yield original
    finally:
        schema.write_bytes(original)
        backup.unlink(missing_ok=True)


@contextmanager
def active_test_set(test_set: Path, schema: Path = ACTIVE_SCHEMA):
    """Temporarily make `test_set` the active properties file `schema`."""
    with protected_schema(schema):
        shutil.copyfile(test_set, schema)
        yield


def fresh_copy(source: Path, name: str) -> Path:
    """Copy a database to data/experiment_dbs/<name>.duckdb (overwriting an older copy)."""
    WORK_DB_DIR.mkdir(parents=True, exist_ok=True)
    dest = WORK_DB_DIR / f"{name}.duckdb"
    shutil.copyfile(source, dest)
    return dest


def run_dbt(command: str, db_path: Path, run_name: str, log_dir: Path,
            models: tuple[str, ...] = DEFAULT_MODELS, extra: list[str] | None = None,
            dbt_args: list[str] | None = None) -> RunResult:
    """Run `dbt <command>` (test or parse) on db_path. The caller sets the active test set.

    dbt_args selects the dataset (for example ["--vars", "{dataset: olist}"]).
    """
    target_dir = DBT_DIR / "target" / "runs" / run_name
    started = time.time()
    cmd = [dbt_executable(), command, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
           "--target-path", str(target_dir), "--no-partial-parse"]
    if command == "test":
        cmd += ["--select", *models]
    cmd += (dbt_args or []) + (extra or [])
    env = os.environ.copy()
    env["DUCKDB_PATH"] = db_path.as_posix()
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env,
                              timeout=DBT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"dbt {command} for {run_name} did not finish within {DBT_TIMEOUT_SECONDS}s "
                           "(set DBT_TIMEOUT_SECONDS to change). Results saved so far are kept; "
                           "run the same command again to continue.") from None

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
