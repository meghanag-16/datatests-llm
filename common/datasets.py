"""Datasets the tool can run on, and where each one keeps its files.

Every command that works on one dataset takes --dataset (default: football) and reads its
paths from here, so the football reproduction keeps its original file locations and a new
dataset only needs an entry below plus its dbt models and configs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt_project"


@dataclass(frozen=True)
class Dataset:
    name: str
    models: tuple[str, ...]          # models the tests are written for
    warehouse: Path                  # DuckDB file that dbt builds
    snapshot: Path                   # clean copy used by every experiment
    test_sets: Path                  # folder with test set files
    active_schema: Path              # properties file that a test set is swapped into
    mutation_config: Path
    mutants: Path                    # folder for mutated databases and manifest.jsonl
    scoring_config: Path             # held-out operator split
    dbt_vars: dict = field(default_factory=dict)

    @property
    def manual_baseline(self) -> Path:
        return self.test_sets / "manual_baseline.yml"

    @property
    def manifest(self) -> Path:
        return self.mutants / "manifest.jsonl"

    def report_dir(self, kind: str) -> Path:
        """reports/<kind> for football (original layout), reports/<kind>/<dataset> otherwise."""
        base = ROOT / "reports" / kind
        return base if self.name == "football" else base / self.name

    def dbt_args(self) -> list[str]:
        """Extra dbt arguments that select this dataset's models."""
        if not self.dbt_vars:
            return []
        inner = ", ".join(f"{k}: {v}" for k, v in self.dbt_vars.items())
        return ["--vars", "{" + inner + "}"]


DATASETS: dict[str, Dataset] = {
    "football": Dataset(
        name="football",
        models=("dim_teams", "fct_matches", "fct_training_dataset"),
        warehouse=ROOT / "data" / "warehouse.duckdb",
        snapshot=ROOT / "data" / "snapshots" / "dbt.clean.duckdb",
        test_sets=DBT_DIR / "test_sets",
        active_schema=DBT_DIR / "models" / "marts" / "schema.yml",
        mutation_config=ROOT / "mutations" / "config" / "football.yml",
        mutants=ROOT / "data" / "mutants",
        scoring_config=ROOT / "scoring" / "config.yml",
    ),
    "olist": Dataset(
        name="olist",
        models=("dim_customers", "dim_products", "dim_sellers", "fct_orders", "fct_order_items"),
        warehouse=ROOT / "data" / "olist.duckdb",
        snapshot=ROOT / "data" / "snapshots" / "olist.clean.duckdb",
        test_sets=DBT_DIR / "test_sets" / "olist",
        active_schema=DBT_DIR / "models" / "olist" / "marts" / "schema.yml",
        mutation_config=ROOT / "mutations" / "config" / "olist.yml",
        mutants=ROOT / "data" / "mutants" / "olist",
        scoring_config=ROOT / "scoring" / "config_olist.yml",
        dbt_vars={"dataset": "olist"},
    ),
}


def get(name: str) -> Dataset:
    try:
        return DATASETS[name]
    except KeyError:
        raise SystemExit(f"Unknown dataset '{name}'. Choose from: {', '.join(DATASETS)}") from None


def resolve_file(path_text: str) -> Path:
    """A path stored relative to the project root, written on Windows or Linux."""
    p = Path(path_text.replace("\\", "/"))
    return p if p.is_absolute() else ROOT / p
