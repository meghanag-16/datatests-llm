"""Build the prompt that asks an LLM for dbt data tests for one table."""
from __future__ import annotations

from .context import TableContext

SYSTEM_PROMPT = (
    "You are a senior analytics engineer who writes dbt data tests. "
    "Answer with one YAML document only, inside a ```yaml code block, and nothing else."
)

ALLOWED_TESTS = """\
Column-level tests (put them under the column's data_tests):
  - not_null
  - unique
  - accepted_values:   arguments: {values: [list of allowed values]}
  - relationships:     arguments: {to: "ref('<other_model>')", field: <column in that model>}
  - in_range:          arguments: {min_value: <number or omit>, max_value: <number or omit>}
Model-level tests (put them under the model's data_tests):
  - expression_is_true: arguments: {expression: "<SQL boolean expression over this model's columns>"}"""

OUTPUT_FORMAT = """\
version: 2
models:
  - name: <model name>
    data_tests:            # optional, model-level tests
      - expression_is_true:
          arguments:
            expression: "..."
    columns:
      - name: <column name>
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: ["A", "B"]"""


def build_prompt(target: TableContext, others: dict[str, TableContext]) -> str:
    columns = "\n".join(f"  - {c.name}: {c.type}" for c in target.columns)
    other_models = "\n".join(
        f"  - {t.name}: {', '.join(t.column_names)}" for name, t in others.items() if name != target.name
    ) or "  (none)"
    return f"""Write dbt data tests for the model `{target.name}` in a DuckDB warehouse.
The tests should catch real data-quality problems (missing values, duplicate keys, invalid
categories, impossible numbers, broken references, wrong dates, inconsistent rows) without
failing on the current, correct data.

Model: {target.name}
Rows: {target.row_count}
Columns:
{columns}

Sample rows (CSV, NULL means missing):
{target.sample_as_csv()}

Other models that may be referenced with relationships tests:
{other_models}

Use only these test types:
{ALLOWED_TESTS}

Rules:
- Use only the columns listed above.
- Only add a test if the sample data and column meaning support it.
- Do not repeat the same test on the same column.

Return YAML in exactly this structure:
{OUTPUT_FORMAT}
"""
