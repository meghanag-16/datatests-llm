"""Parse LLM output into dbt tests, check every test, and write a clean dbt YAML file.

Each test becomes a TestSpec with a stable id, so the same test keeps the same id across
runs and models. Tests that cannot be valid (unknown column, unknown test type, missing
arguments, SQL that does not compile) are dropped with a reason instead of silently.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb
import yaml

from .context import TableContext

COLUMN_TESTS = {"not_null", "unique", "accepted_values", "relationships", "in_range"}
MODEL_TESTS = {"expression_is_true"}
NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
                 "UINTEGER", "UBIGINT", "FLOAT", "REAL", "DOUBLE", "DECIMAL", "NUMERIC")
REF_PATTERN = re.compile(r"""^\s*ref\(\s*['"]([A-Za-z0-9_]+)['"]\s*\)\s*$""")


@dataclass
class TestSpec:
    __test__ = False  # not a pytest test class
    model: str
    column: str | None          # None for model-level tests
    type: str
    args: dict = field(default_factory=dict)
    source: str = "llm"         # llm | manual
    test_id: str = ""

    def signature(self) -> str:
        return json.dumps([self.model, self.column, self.type, self.args], sort_keys=True, default=str)

    def __post_init__(self) -> None:
        if not self.test_id:
            digest = hashlib.sha1(self.signature().encode("utf-8")).hexdigest()[:8]
            self.test_id = f"{self.model}.{self.column or '_model'}.{self.type}.{digest}"


@dataclass
class Rejected:
    model: str
    column: str | None
    raw: str
    reason: str


# ------------------------------------------------------------------ parsing

def extract_yaml(text: str) -> str:
    """Take the YAML out of a reply, with or without a ```yaml fence."""
    fenced = re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", text, flags=re.S | re.I)
    return max(fenced, key=len) if fenced else text


def parse_yaml(text: str) -> list[dict]:
    """Return the list of model entries, or raise ValueError."""
    try:
        doc = yaml.safe_load(extract_yaml(text))
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML does not parse: {str(exc).splitlines()[0]}") from exc
    if isinstance(doc, list):
        doc = {"models": doc}
    if not isinstance(doc, dict) or not isinstance(doc.get("models"), list):
        raise ValueError("YAML has no 'models' list")
    return [m for m in doc["models"] if isinstance(m, dict)]


def _split_test(entry) -> tuple[str | None, dict]:
    """A test entry is 'not_null' or {'accepted_values': {...}}. Returns (type, args)."""
    if isinstance(entry, str):
        return entry.strip(), {}
    if isinstance(entry, dict) and len(entry) == 1:
        name, body = next(iter(entry.items()))
        body = body or {}
        if not isinstance(body, dict):
            return str(name), {"__invalid__": body}
        args = dict(body.get("arguments") or {})
        for k, v in body.items():  # older style: arguments written directly under the test
            if k not in {"arguments", "config", "name", "description", "meta", "severity"}:
                args.setdefault(k, v)
        return str(name).strip(), args
    return None, {}


def _tests_of(entry: dict) -> list:
    tests = entry.get("data_tests", entry.get("tests")) or []
    return tests if isinstance(tests, list) else []


def raw_tests(models: list[dict]) -> list[tuple[str, str | None, object]]:
    """Flatten model entries into (model, column, raw test entry)."""
    out = []
    for m in models:
        name = str(m.get("name", "")).strip()
        for t in _tests_of(m):
            out.append((name, None, t))
        for c in m.get("columns") or []:
            if isinstance(c, dict):
                for t in _tests_of(c):
                    out.append((name, str(c.get("name", "")).strip(), t))
    return out


# ------------------------------------------------------------------ checking

def check_test(model: str, column: str | None, entry, contexts: dict[str, TableContext],
               con: duckdb.DuckDBPyConnection | None, source: str = "llm") -> TestSpec | Rejected:
    raw = json.dumps(entry, default=str)
    reject = lambda reason: Rejected(model, column, raw, reason)
    ttype, args = _split_test(entry)
    if model not in contexts:
        return reject(f"unknown model '{model}'")
    ctx = contexts[model]
    if not ttype:
        return reject("test entry is not a name or a single-key mapping")
    if "__invalid__" in args:
        return reject("test arguments are not a mapping")
    if ttype not in COLUMN_TESTS | MODEL_TESTS:
        return reject(f"test type '{ttype}' is not allowed")
    if ttype in COLUMN_TESTS and column is None:
        return reject(f"'{ttype}' must be attached to a column")
    if ttype in MODEL_TESTS and column is not None:
        # Accept it, but store it as the model-level test it really is.
        column = None
    if column is not None and column not in ctx.column_names:
        return reject(f"unknown column '{column}'")

    clean: dict = {}
    if ttype == "accepted_values":
        values = args.get("values")
        if not isinstance(values, list) or not values or any(isinstance(v, (list, dict)) for v in values):
            return reject("accepted_values needs a non-empty list of plain values")
        clean["values"] = values
        if "quote" in args:
            clean["quote"] = bool(args["quote"])
    elif ttype == "relationships":
        to, fld = args.get("to"), args.get("field")
        match = REF_PATTERN.match(str(to or ""))
        parent = match.group(1) if match else str(to or "").strip()
        if parent not in contexts:
            return reject(f"relationships target '{to}' is not a known model")
        if fld not in contexts[parent].column_names:
            return reject(f"relationships field '{fld}' is not a column of {parent}")
        clean = {"to": f"ref('{parent}')", "field": fld}
    elif ttype == "in_range":
        col_type = next(c.type for c in ctx.columns if c.name == column).upper()
        if not col_type.startswith(NUMERIC_TYPES):
            return reject(f"in_range on non-numeric column ({col_type})")
        for bound in ("min_value", "max_value"):
            if args.get(bound) is not None:
                if isinstance(args[bound], bool) or not isinstance(args[bound], (int, float)):
                    return reject(f"in_range {bound} is not a number")
                clean[bound] = args[bound]
        if not clean:
            return reject("in_range needs min_value or max_value")
    elif ttype == "expression_is_true":
        expr = args.get("expression")
        if not isinstance(expr, str) or not expr.strip():
            return reject("expression_is_true needs an expression")
        if ";" in expr:
            return reject("expression contains ';'")
        if con is not None:
            try:
                con.execute(f'select count(*) from "{model}" where not ({expr})').fetchone()
            except duckdb.Error as exc:
                return reject(f"expression does not run: {str(exc).splitlines()[0][:150]}")
        clean["expression"] = expr.strip()
    return TestSpec(model=model, column=column, type=ttype, args=clean, source=source)


def validate_reply(text: str, expected_model: str, contexts: dict[str, TableContext],
                   con: duckdb.DuckDBPyConnection | None) -> tuple[list[TestSpec], list[Rejected]]:
    models = parse_yaml(text)
    kept: list[TestSpec] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    for model, column, entry in raw_tests(models):
        if model != expected_model:
            rejected.append(Rejected(model, column, json.dumps(entry, default=str),
                                     f"model '{model}' was not the one asked for ({expected_model})"))
            continue
        result = check_test(model, column, entry, contexts, con)
        if isinstance(result, Rejected):
            rejected.append(result)
        elif result.test_id in seen:
            rejected.append(Rejected(model, column, json.dumps(entry, default=str), "exact duplicate in the reply"))
        else:
            seen.add(result.test_id)
            kept.append(result)
    return kept, rejected


def load_test_set(path: Path, contexts: dict[str, TableContext], source: str) -> list[TestSpec]:
    """Read an existing dbt YAML test set (for example the manual baseline)."""
    specs = []
    for model, column, entry in raw_tests(parse_yaml(path.read_text(encoding="utf-8"))):
        result = check_test(model, column, entry, contexts, None, source=source)
        if isinstance(result, TestSpec):
            specs.append(result)
    return specs


# ------------------------------------------------------------------ writing

def to_yaml(specs: list[TestSpec], model_order: list[str], header: str = "", meta: dict | None = None) -> str:
    models = []
    for model in model_order:
        mine = [s for s in specs if s.model == model]
        if not mine:
            continue
        entry: dict = {"name": model}

        def emit(s: TestSpec) -> dict:
            body: dict = {}
            if s.args:
                body["arguments"] = s.args
            body["config"] = {"meta": {"test_id": s.test_id, "source": s.source, **(meta or {})}}
            return {s.type: body}

        model_tests = [emit(s) for s in mine if s.column is None]
        if model_tests:
            entry["data_tests"] = model_tests
        columns = []
        for col in dict.fromkeys(s.column for s in mine if s.column is not None):
            columns.append({"name": col, "data_tests": [emit(s) for s in mine if s.column == col]})
        if columns:
            entry["columns"] = columns
        models.append(entry)
    body = yaml.safe_dump({"version": 2, "models": models}, sort_keys=False, allow_unicode=True, width=120)
    return (header.rstrip() + "\n\n" if header else "") + body


def specs_to_dicts(specs) -> list[dict]:
    return [asdict(s) for s in specs]
