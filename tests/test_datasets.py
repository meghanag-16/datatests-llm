from pathlib import Path

import pytest

from common import datasets
from common.dbt_runner import active_test_set, protected_schema


def test_football_keeps_original_locations():
    ds = datasets.get("football")
    assert ds.snapshot == datasets.ROOT / "data" / "snapshots" / "dbt.clean.duckdb"
    assert ds.manifest == datasets.ROOT / "data" / "mutants" / "manifest.jsonl"
    assert ds.test_sets == datasets.DBT_DIR / "test_sets"
    assert ds.report_dir("scoring") == datasets.ROOT / "reports" / "scoring"
    assert ds.dbt_args() == []


def test_olist_has_its_own_locations():
    ds = datasets.get("olist")
    assert ds.test_sets.name == "olist"
    assert ds.report_dir("scoring") == datasets.ROOT / "reports" / "scoring" / "olist"
    assert ds.dbt_args() == ["--vars", "{dataset: olist}"]
    assert ds.mutation_config.exists() and ds.scoring_config.exists() and ds.manual_baseline.exists()


def test_unknown_dataset_is_rejected():
    with pytest.raises(SystemExit):
        datasets.get("nope")


def test_manifest_paths_from_windows_and_linux_resolve_the_same():
    a = datasets.resolve_file("data\\mutants\\x.duckdb")
    b = datasets.resolve_file("data/mutants/x.duckdb")
    assert a == b == datasets.ROOT / "data" / "mutants" / "x.duckdb"


def test_schema_is_restored_after_an_error(tmp_path: Path):
    schema, test_set = tmp_path / "schema.yml", tmp_path / "set.yml"
    schema.write_text("original")
    test_set.write_text("tests")
    with pytest.raises(RuntimeError):
        with active_test_set(test_set, schema):
            assert schema.read_text() == "tests"
            raise RuntimeError("dbt crashed")
    assert schema.read_text() == "original"
    assert not (tmp_path / "schema.yml.bak").exists()


def test_schema_left_by_a_killed_run_is_recovered(tmp_path: Path):
    schema = tmp_path / "schema.yml"
    schema.write_text("swapped-in test set")             # a killed run left this behind
    (tmp_path / "schema.yml.bak").write_text("original")  # together with its backup
    with protected_schema(schema) as original:
        assert original == b"original"
    assert schema.read_text() == "original"


def test_a_second_run_is_refused_while_the_first_holds_the_lock(tmp_path: Path):
    schema = tmp_path / "schema.yml"
    schema.write_text("original")
    with protected_schema(schema):
        schema.write_text("first run's test set")
        with pytest.raises(SystemExit):
            with protected_schema(schema):
                pass
        assert schema.read_text() == "first run's test set"   # the first run is left alone
    assert schema.read_text() == "original"
    assert not (tmp_path / "schema.yml.lock").exists()


def test_a_lock_left_by_a_killed_run_is_taken_over(tmp_path: Path):
    import os
    import time
    schema, lock = tmp_path / "schema.yml", tmp_path / "schema.yml.lock"
    schema.write_text("original")
    lock.write_text("old host pid 1")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with protected_schema(schema) as original:
        assert original == b"original"
    assert not lock.exists()


def test_schema_is_restored_when_the_run_is_stopped_with_sigterm(tmp_path: Path):
    import signal
    schema = tmp_path / "schema.yml"
    schema.write_text("original")
    with pytest.raises(SystemExit):
        with protected_schema(schema):
            schema.write_text("swapped-in test set")
            # What Airflow sends when it stops a task. raise_signal runs the Python handler on every
            # OS; os.kill(own pid, SIGTERM) on Windows ends the process at once without running it.
            signal.raise_signal(signal.SIGTERM)
    assert schema.read_text() == "original"
    assert not (tmp_path / "schema.yml.bak").exists()
    assert not (tmp_path / "schema.yml.lock").exists()
