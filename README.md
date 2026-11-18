# datatests-llm

Data-quality testing for dbt pipelines with LLM-generated tests, evaluated by anomaly injection.

The project starts from an exact reproduction of a published multi-layer data-quality framework and extends it toward automatically keeping only the generated tests that catch real problems.

## Background and credit

This work reproduces and builds on:

> Ismail Gargouri and Hassan Reza, *A Multi-Layer Testing Framework for Automated Data Quality Assurance in Cloud-Native ELT Pipelines*, arXiv:2605.20500, 2026.
>
> Code: [igargouri10/football-analytics-platform](https://github.com/igargouri10/football-analytics-platform) (MIT licence)

The dbt models, the manual and expanded test sets, the authors' LLM-generated test set and the 16 anomaly injections are adapted from the authors' repository. Files that reuse their code say so in a header comment.

Related work: T. Rehberger, T. Hütter, L. Ehrlinger and W. Wöß, *Evaluating Data Quality Tools: Measurement Capabilities and LLM Integration*, arXiv:2604.09163, 2026. It finds that LLM integration in current data-quality tools is limited to rule creation, with no direct data validation by LLMs.

## What is done

**Reproduction of the paper's main result** on English Premier League 2023-24 data (100 matches, 20 teams):

| Test set | Batch A (key integrity) | Batch B (semantic/domain) | Batch C (mixed) | Total | Paper |
|---|---|---|---|---|---|
| Manual-only baseline | 4/4 | 0/6 | 3/6 | 7/16 | 7/16 |
| Manual-expanded | 4/4 | 6/6 | 6/6 | 16/16 | 16/16 |
| Manual + authors' LLM tests | 4/4 | 6/6 | 6/6 | 16/16 | 16/16 |

Differences from the original setup:
- Data is stored locally in DuckDB instead of S3 and Snowflake.
- TheSportsDB's original test key is no longer available. The same 100 matches (rounds 1 to 10) are fetched round by round with the current free key.
- The API now reports finished matches as `FT`. This is mapped to the original value `Match Finished`.

## Planned

- Generate dbt tests with more than one LLM (Gemini, Groq / OpenRouter models).
- Replace the 16 hand-made anomalies with mutation operators (nulls, duplicate keys, invalid categories, out-of-range values, orphaned rows, date shifts, double counting).
- Score every test against the mutations, drop tests that never fail or only repeat other tests, and pick a minimal test set.
- Measure the kept tests on mutation types not used for selection, to avoid the evaluation bias noted in the paper.
- Repeat on a second domain (Olist e-commerce data).
- Package as a CLI, a GitHub Action and an HTML report.

## Stack

Python 3.12, uv, DuckDB, dbt (dbt-duckdb), Apache Airflow (Docker), Gemini / Groq / OpenRouter.

## Project layout

```
dbt_project/
  models/staging/      stg_matches
  models/marts/        dim_teams, fct_matches, fct_training_dataset
  test_sets/           manual_baseline, manual_expanded, authors_llm
experiments/           anomaly injection experiment
llm_tests/             LLM provider layer (timeouts and fallback across providers)
scripts/               download, load, checks, clean snapshot
data/                  raw files and DuckDB databases (not committed)
reports/               experiment outputs (not committed)
```

## How to run

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # only needed for the LLM steps; add API keys there

# 1. Data and models
uv run python scripts/download_epl.py
uv run python scripts/load_epl.py
uv run dbt build --project-dir dbt_project --profiles-dir dbt_project --full-refresh
uv run python scripts/check_football.py
uv run python scripts/snapshot_clean.py

# 2. Anomaly experiment (reproduces 7/16 vs 16/16)
uv run python experiments/run_anomaly_experiment.py
```

Results are written to `reports/anomaly_results/` (`final_summary.json`, `detections.csv` and dbt logs).

Optional LLM provider check: `uv run python scripts/check_llm.py`

## Licence

The code adapted from the authors' repository remains under its MIT licence. See [THIRD_PARTY_LICENSE](THIRD_PARTY_LICENSE) for the licence text and the list of adapted files.
