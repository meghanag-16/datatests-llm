"""Airflow DAGs for the project: the paper reproduction and the extension, one DAG each.

reproduction_football  The paper's experiment (Gargouri & Reza, arXiv:2605.20500), run in
                       Airflow as the authors did, but locally (no S3, no Snowflake):
  ingest -> build_models -> check_models -> snapshot_clean -> anomaly_experiment
         -> generate_tests -> anomaly_experiment_llm -> stability (optional)

extension_football / extension_olist  LLM tests scored and pruned with mutation testing:
  ingest -> build_models -> check_models -> snapshot_clean -+-> generate_tests -+-> score_tests
                                                            +-> create_mutants -+

Each task runs one of the project's own commands inside the container, with the project's
Python environment (/opt/data_eng_env) and the project folder mounted at /opt/data_eng, so a
DAG run does exactly what the commands in the README do by hand. The two football DAGs use
the same files, so run them one after the other, not at the same time.

Trigger with parameters (Trigger DAG with config in the UI):
  provider       gemini | groq | openrouter | auto (auto = LLM_PROVIDER, then the fallback order)
  trial          0 reuses cached LLM replies; another number asks the LLM for a fresh set
  run_stability  reproduction only: also run the five-trial stability check (slow; needs a
                 named provider, not auto)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

PROJECT = os.environ.get("DATA_ENG_HOME", "/opt/data_eng")
PY = os.environ.get("DATA_ENG_PYTHON", "/opt/data_eng_env/bin/python")
# The project's environment first on PATH, so the dbt it installs is the one that runs.
TASK_ENV = {"PATH": f"{os.path.dirname(PY)}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "PYTHONUNBUFFERED": "1"}

# Name of the generated test set: llm_<provider>, plus _trial<N> for a fresh generation.
TEST_SET_NAME = ("llm_{{ params.provider }}"
                 "{% if params.trial | int > 0 %}_trial{{ params.trial }}{% endif %}")
PROVIDER_FLAG = "{% if params.provider != 'auto' %}--provider {{ params.provider }}{% endif %}"

DEFAULT_ARGS = {"retries": 0, "execution_timeout": timedelta(hours=3)}
COMMON_PARAMS = {
    "provider": Param("gemini", type="string", enum=["gemini", "groq", "openrouter", "auto"]),
    "trial": Param(0, type="integer", minimum=0),
}

# Steps that differ between datasets. Football downloads its data; the Olist CSV files have
# to be downloaded from Kaggle once by hand (see scripts/load_olist.py).
INGEST = {
    "football": f"{PY} scripts/download_epl.py && {PY} scripts/load_epl.py",
    "olist": f"{PY} scripts/load_olist.py",
}
CHECK = {
    "football": f"{PY} scripts/check_football.py",
    "olist": f"{PY} scripts/check_olist.py",
}


def bash(task_id: str, command: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=task_id, bash_command=command, cwd=PROJECT,
                        env=TASK_ENV, append_env=True, **kwargs)


def prepare(dataset: str):
    """ingest -> build_models -> check_models -> snapshot_clean; returns (first, last) task."""
    flag = "" if dataset == "football" else f" --dataset {dataset}"
    ingest = bash("ingest", INGEST[dataset])
    build = bash("build_models", f"{PY} scripts/build_dataset.py{flag}")
    check = bash("check_models", CHECK[dataset])
    snapshot = bash("snapshot_clean", f"{PY} scripts/snapshot_clean.py{flag}")
    ingest >> build >> check >> snapshot
    return ingest, snapshot


# ---------------------------------------------------------------- reproduction

with DAG(
    dag_id="reproduction_football",
    description="Paper reproduction: 16 injected anomalies, manual vs expanded vs LLM tests",
    start_date=datetime(2026, 1, 1),
    schedule=None,                 # run on demand
    catchup=False,
    max_active_runs=1,
    tags=["reproduction", "football"],
    default_args=DEFAULT_ARGS,
    params={**COMMON_PARAMS, "run_stability": Param(False, type="boolean")},
    doc_md=__doc__,
) as reproduction:
    _, snapshot = prepare("football")
    # The paper's three conditions with the authors' own test sets (expected 7/16, 16/16, 16/16).
    anomaly = bash("anomaly_experiment", f"{PY} experiments/run_anomaly_experiment.py")
    generate = bash("generate_tests",
                    f"{PY} -m llm_tests.generate {PROVIDER_FLAG} "
                    f"--trial {{{{ params.trial }}}} --name {TEST_SET_NAME}")
    # The same experiment with our generated tests in place of the authors' LLM tests.
    anomaly_llm = bash("anomaly_experiment_llm",
                       f"{PY} experiments/run_anomaly_experiment.py --conditions manual_only manual_expanded "
                       f"--test-set manual_plus_{TEST_SET_NAME}.yml "
                       f"--out reports/anomaly_results/{TEST_SET_NAME}")
    stability = bash("stability",
                     "{% if params.run_stability and params.provider != 'auto' %}"
                     f"{PY} experiments/run_stability.py --provider {{{{ params.provider }}}}"
                     "{% else %}echo 'Stability check skipped (set run_stability and a named provider).'"
                     "{% endif %}")
    # Strictly one after another: these steps take turns swapping test sets into the same file.
    snapshot >> anomaly >> generate >> anomaly_llm >> stability


# ---------------------------------------------------------------- extension

for name in ("football", "olist"):
    flag = "" if name == "football" else f" --dataset {name}"
    with DAG(
        dag_id=f"extension_{name}",
        description=f"Extension ({name}): LLM tests scored and pruned with mutation testing",
        start_date=datetime(2026, 1, 1),
        schedule=None,
        catchup=False,
        max_active_runs=1,
        tags=["extension", name],
        default_args=DEFAULT_ARGS,
        params=COMMON_PARAMS,
        doc_md=__doc__,
    ) as dag:
        _, snapshot = prepare(name)
        generate = bash("generate_tests",
                        f"{PY} -m llm_tests.generate{flag} {PROVIDER_FLAG} "
                        f"--trial {{{{ params.trial }}}} --name {TEST_SET_NAME}")
        mutate = bash("create_mutants", f"{PY} -m mutations.run{flag}")
        score = bash("score_tests",
                     f"{PY} -m scoring.run{flag} --test-set manual_baseline.yml {TEST_SET_NAME}.yml")
        snapshot >> [generate, mutate] >> score
    globals()[dag.dag_id] = dag
