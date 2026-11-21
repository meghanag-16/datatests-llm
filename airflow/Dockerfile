# Airflow image with this project's Python environment added.
# The project's packages (dbt, DuckDB, LLM clients) are installed into their own virtual
# environment from uv.lock, separate from Airflow's packages, so the two never conflict.
FROM apache/airflow:3.3.2-python3.12

USER root
RUN mkdir -p /opt/data_eng_env && chown airflow:0 /opt/data_eng_env

USER airflow
RUN pip install --no-cache-dir uv==0.13.5
COPY --chown=airflow:0 pyproject.toml uv.lock .python-version /tmp/data_eng/
RUN cd /tmp/data_eng \
 && UV_PROJECT_ENVIRONMENT=/opt/data_eng_env python -m uv sync --frozen --no-dev \
      --python python3.12 --no-python-downloads

ENV DATA_ENG_PYTHON=/opt/data_eng_env/bin/python \
    DATA_ENG_HOME=/opt/data_eng
