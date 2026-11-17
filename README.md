# data_eng: LLM-generated dbt tests, pruned by mutation testing

Status: environment setup. See the project brief:
"Data engineering LLM-generated data.txt".

## Folder layout
- dbt_project/  dbt project (DuckDB). Run dbt from this folder's parent with
  `--project-dir dbt_project --profiles-dir dbt_project`.
- llm_tests/    LLM provider layer (Gemini default, OpenAI-compatible optional) and, later, test generation.
- mutations/    Mutation operators.
- scoring/      Test-by-mutation scoring, pruning and held-out evaluation.
- airflow/      Airflow docker-compose and DAGs.
- cli/          Command line tool.
- data/raw/     Downloaded datasets (not committed).
- reports/      Generated HTML reports (not committed).
- scripts/      Setup checks.
- tests/        Python unit tests.

## Setup (uv)
Dependencies live in pyproject.toml and are locked in uv.lock (commit both).
- `uv sync` creates .venv with Python 3.12 and installs everything.
- Add a package: `uv add <name>` (dev only: `uv add --dev <name>`).
- Run anything inside the environment: `uv run <command>`. No activation needed.
requirements.txt is kept only as a fallback for plain pip.

## LLM providers
Put your keys in .env (copy from .env.example). Switch provider by changing one line:
`LLM_PROVIDER=gemini`, `groq`, `openrouter` or `openai_compat`.
- List current model names: `uv run python scripts/list_models.py <provider>`
  (OpenRouter free models only: `uv run python scripts/list_models.py openrouter free`)
- Fallback: `LLM_PROVIDER` is tried first, then `LLM_FALLBACK_ORDER` (default gemini,groq,openrouter),
  skipping providers without a key. Each request times out after `LLM_TIMEOUT_SECONDS` (30) and gets at most
  `LLM_MAX_RETRIES` (1) quick retry before moving to the next provider. The answering provider and model are
  printed and stored on the LLM object (`last_provider`, `last_model`).
- Test with fallback: `uv run python scripts/check_llm.py`
- Test one provider only: `uv run python scripts/check_llm.py <provider>`
- Test every provider with a key: `uv run python scripts/check_llm.py all`

## Setup checks (run from the data_eng folder)
1. `uv run python scripts/check_env.py`
2. `uv run dbt debug --project-dir dbt_project --profiles-dir dbt_project`
3. `uv run dbt build --project-dir dbt_project --profiles-dir dbt_project`
4. `uv run python scripts/check_llm.py`
