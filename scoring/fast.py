"""Fast scoring engine: compile the candidate tests once with dbt, then run their SQL in DuckDB.

The standard engine starts dbt once per mutated database (parse the project, connect, run every
test). Most of that time is dbt starting up, not the tests. Here dbt runs only once, to compile
the tests, and every mutant is checked by running the compiled queries directly in DuckDB,
wrapped the same way dbt wraps a data test:

    select <fail_calc> as failures, <fail_calc> <warn_if> as should_warn,
           <fail_calc> <error_if> as should_error from (<compiled test>) dbt_internal_test

Status rules follow dbt: severity error -> fail when should_error, warn when only should_warn,
otherwise pass; a query that raises is an error. Models are materialized as tables and the
mutated databases are copies of the clean snapshot, so the same SQL runs on each of them once
the database name dbt compiled into it is removed.

scoring/verify_fast.py checks that this engine gives the same statuses as dbt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

from common import datasets
from common.dbt_runner import DBT_DIR, fresh_copy, run_dbt

COMPILE_DB_STEM = "fastscore_compile"   # database name dbt writes into the compiled SQL


@dataclass
class CompiledTest:
    test_id: str
    query: str          # the full wrapped query; returns failures, should_warn, should_error
    severity: str


def _wrap(sql: str, cfg: dict) -> str:
    fail_calc = cfg.get("fail_calc") or "count(*)"
    warn_if = cfg.get("warn_if") or "!= 0"
    error_if = cfg.get("error_if") or "!= 0"
    limit = f"\nlimit {int(cfg['limit'])}" if cfg.get("limit") else ""
    return (f"select {fail_calc} as failures, {fail_calc} {warn_if} as should_warn, "
            f"{fail_calc} {error_if} as should_error\nfrom (\n{sql}{limit}\n) dbt_internal_test")


def compile_tests(ds: datasets.Dataset, run_name: str, log_dir: Path) -> list[CompiledTest]:
    """dbt compile with the active test set (the caller must have swapped it in)."""
    db = fresh_copy(ds.snapshot, COMPILE_DB_STEM)
    run = run_dbt("compile", db, run_name, log_dir, ds.models,
                  extra=["--select", *ds.models], dbt_args=ds.dbt_args())
    manifest_path = DBT_DIR / "target" / "runs" / run_name / "manifest.json"
    if run.returncode != 0 or not manifest_path.exists():
        raise RuntimeError(f"dbt compile failed; see {run.log_file}")
    nodes = json.loads(manifest_path.read_text(encoding="utf-8")).get("nodes", {})
    catalog = re.compile(r'"%s"\.' % re.escape(COMPILE_DB_STEM), flags=re.I)
    tests = []
    for node in nodes.values():
        if node.get("resource_type") != "test":
            continue
        cfg = node.get("config", {}) or {}
        test_id = (cfg.get("meta") or node.get("meta") or {}).get("test_id")
        sql = node.get("compiled_code")
        if not test_id or not sql:
            continue
        if cfg.get("where"):
            raise RuntimeError(f"{test_id}: tests with a 'where' config are not supported by the fast engine")
        tests.append(CompiledTest(test_id, _wrap(catalog.sub("", sql), cfg),
                                  (cfg.get("severity") or "ERROR").upper()))
    return tests


def run_tests(db_path: Path, tests: list[CompiledTest]) -> dict[str, str]:
    """test_id -> pass | warn | fail | error on one database (opened read-only)."""
    con = duckdb.connect(str(db_path), read_only=True, config={"threads": 1})
    status: dict[str, str] = {}
    try:
        for t in tests:
            try:
                failures, should_warn, should_error = con.execute(t.query).fetchone()
            except duckdb.Error:
                status[t.test_id] = "error"
                continue
            if t.severity == "ERROR" and should_error:
                status[t.test_id] = "fail"
            elif should_warn:
                status[t.test_id] = "warn"
            else:
                status[t.test_id] = "pass"
    finally:
        con.close()
    return status
