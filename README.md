# datatests-llm

LLM-generated dbt data tests, checked by damaging the data on purpose and keeping only the tests that catch something.

## What the project does

1. **Reproduces a published data-quality framework.** Gargouri & Reza (arXiv:2605.20500) test a football dbt pipeline with manual tests and GPT-4.1-mini-generated tests against 16 injected anomalies. The same models, tests and anomalies run here on DuckDB.
2. **Generates dbt tests with free LLMs.** For each model, the column names, types, row count and sample rows go to Gemini 2.5 Flash or gpt-oss-120b (on Groq). Every proposed test is validated (columns, test types, arguments, SQL) before it is kept.
3. **Damages copies of the data in known ways.** Seven mutation operators (nulls, duplicate keys, invalid categories, out-of-range numbers, orphaned rows, shifted dates, double-loaded rows) create a few hundred mutated databases per dataset.
4. **Scores and prunes the tests.** Every test runs on every mutated database. Tests are classed as useful, redundant, low-value or broken, and a greedy set cover keeps the smallest set that still catches every detectable mutant.
5. **Checks whether the pruned set holds up on new kinds of damage.** The minimal set is chosen on some mutation types and measured on types it never saw (a fixed split and a leave-one-type-out rotation). The paper judges its tests on the same anomalies it uses to choose them, so it cannot measure this.
6. **Runs on two domains and in Airflow.** Football (the paper's data) and Olist e-commerce (five dbt models from a 5,000-order sample of 99,441 orders), orchestrated by Airflow DAGs in Docker. Every step also runs by hand.

## Main results

Details in [RESULTS.md](RESULTS.md); charts in the dashboard (see How to run).

| | Football | Olist |
|---|---|---|
| Paper's 16 anomalies caught: manual only, manual + LLM tests | 7/16 and 16/16 (paper: 7 and 16) | - |
| Mutated databases | 188 | 427 |
| Minimal set (Gemini / gpt-oss-120b) | 18 of 58 / 19 of 54 | 57 of 73 / 56 of 72 |
| Minimal set on unseen damage types, share of the full set's catches | 33-40% | 2.7-2.9% |
| Same, each type held out in turn (average) | 44-46% | 43-45% |

- The paper's main result reproduces exactly, and tests from two free LLMs also reach 16/16, stable over five repeated and five regenerated trials.
- On football about two thirds of the tests can be dropped with no loss of detection; on the richer Olist data most tests are needed.
- A minimal test set does not transfer: chosen on some kinds of damage, it misses most of the damage it was not chosen on. Pruning keeps only what it is measured on.
- Generated tests change from run to run: a fresh generation shares only 78-94% of its tests with the first one, and 15-17 generated tests fail on the real, clean Olist data.
- The fast scoring engine gives exactly the same results as dbt (0 differences in 48,626 test results) and scores 427 databases in under a minute.

## How to run

Requirements: Python 3.12 and uv ; Docker Desktop only for Airflow.

```bash
uv sync
cp .env.example .env          # add API keys here; needed only for test generation
```

Football (the paper's data):

```bash
uv run python scripts/download_epl.py
uv run python scripts/load_epl.py
uv run dbt build --project-dir dbt_project --profiles-dir dbt_project --full-refresh
uv run python scripts/check_football.py
uv run python scripts/snapshot_clean.py

uv run python experiments/run_anomaly_experiment.py                         # reproduction: 7/16 vs 16/16
uv run python -m llm_tests.generate --provider gemini                       # LLM tests
uv run python experiments/run_anomaly_experiment.py --test-set manual_plus_llm_gemini.yml
uv run python -m mutations.run                                              # mutated databases
uv run python -m scoring.run --test-set manual_baseline.yml llm_gemini.yml  # score, prune, held-out
uv run python experiments/run_stability.py --provider gemini                # five-trial stability
```

Olist (download the zip from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce into `data/raw/olist/` first):

```bash
uv run python scripts/load_olist.py
uv run python scripts/build_dataset.py --dataset olist
uv run python scripts/check_olist.py
uv run python scripts/snapshot_clean.py --dataset olist
uv run python -m llm_tests.generate --dataset olist --provider gemini
uv run python -m mutations.run --dataset olist
uv run python -m scoring.run --dataset olist --test-set manual_baseline.yml llm_gemini.yml
```

Dashboard, Airflow and checks:

```bash
uv run streamlit run dashboard/app.py          # results dashboard in the browser
uv run python dashboard/export.py              # refresh the dashboard data after new runs

docker compose -f airflow/docker-compose.yml up -d --build   # UI at http://localhost:8080 (airflow / airflow)
docker compose -f airflow/docker-compose.yml down

uv run pytest
uv run python -m scoring.verify_fast --dataset olist --test-set manual_baseline.yml llm_gemini.yml
```

## Trade-offs

- **DuckDB on a laptop instead of S3 and Snowflake.** Free and quick to rerun, but the paper's DuckDB-to-Snowflake migration and cross-store check are not reproduced.
- **Fast scoring engine.** dbt compiles the tests once and the SQL runs directly in DuckDB, so scoring takes seconds instead of starting dbt for every database. It relies on models being materialised as tables and does not support tests with a `where` config; `--engine dbt` and `scoring.verify_fast` remain for checking.
- **Automatic scoring instead of a manual audit.** Usefulness is measured against hundreds of mutants, which scales and is repeatable, but it is only as good as the mutation operators.
- **Free LLMs with short timeouts.** No cost and a fallback between providers, but rate limits and overloaded models (HTTP 429, 503, 504) can drop a request instead of retrying for long. Replies are cached and calls use temperature 0, so reruns are reproducible.
- **Sampled Olist data.** 5,000 of 99,441 orders keep runs short; results may differ on the full dataset.
- **Airflow in Docker.** Mirrors the paper's orchestration at the cost of a heavier setup; the same steps run without it.

## Limitations

- Seven synthetic mutation operators stand in for real incidents; real data problems can look different.
- A minimal set is not safe to use on its own: it keeps only what the chosen mutation types reward.
- Generated tests that fail on the clean Olist data have not been audited by hand; some may point to real data issues rather than wrong tests.
- Two datasets and two LLMs; mutation scores come from one generation per LLM, while generations vary between runs.
- Counts of useful and redundant tests are computed automatically, so they compare with the paper's manual audit in spirit, not one to one.
- TheSportsDB's original key is gone: the same 100 matches are fetched round by round with the current free key, and status `FT` is mapped to `Match Finished`.

## Credit and licence

Reproduces and builds on Ismail Gargouri and Hassan Reza, *A Multi-Layer Testing Framework for Automated Data Quality Assurance in Cloud-Native ELT Pipelines*, arXiv:2605.20500, 2026 (MIT licence). The football dbt models, the manual and expanded test sets, the authors' LLM test set and the 16 anomaly injections are adapted from that repository; files that reuse their code say so in a header comment, and [THIRD_PARTY_LICENSE](THIRD_PARTY_LICENSE) holds the licence text and the list of adapted files.

Related: T. Rehberger, T. Hütter, L. Ehrlinger and W. Wöß, *Evaluating Data Quality Tools: Measurement Capabilities and LLM Integration*, arXiv:2604.09163, 2026.
