"""Setup check: Python version, packages, DuckDB, and .env presence.

Run from the data_eng folder:  python scripts/check_env.py
"""
import importlib
import os
import sys
from pathlib import Path

ok = True


def report(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"[{'OK' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


v = sys.version_info
report("Python 3.10 to 3.13", (3, 10) <= (v.major, v.minor) <= (3, 13), f"found {v.major}.{v.minor}.{v.micro}")
report("Running inside .venv", sys.prefix != sys.base_prefix, sys.prefix)

for module, pip_name in [
    ("duckdb", "duckdb"),
    ("dbt.version", "dbt-core"),
    ("dbt.adapters.duckdb", "dbt-duckdb"),
    ("pandas", "pandas"),
    ("yaml", "pyyaml"),
    ("google.genai", "google-genai"),
    ("openai", "openai"),
    ("dotenv", "python-dotenv"),
]:
    try:
        importlib.import_module(module)
        report(f"import {pip_name}", True)
    except Exception as exc:  # noqa: BLE001
        report(f"import {pip_name}", False, str(exc))

try:
    import duckdb

    value = duckdb.connect(":memory:").execute("select 42").fetchone()[0]
    report("DuckDB runs a query", value == 42, f"duckdb {duckdb.__version__}")
except Exception as exc:  # noqa: BLE001
    report("DuckDB runs a query", False, str(exc))

root = Path(__file__).resolve().parent.parent
report(".env file exists", (root / ".env").exists(), "copy .env.example to .env")
try:
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    sys.path.insert(0, str(root))
    from llm_tests.providers import key_var_for

    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    key_var = key_var_for(provider)
    key = os.getenv(key_var, "")
    report(f"LLM_PROVIDER={provider}: {key_var} filled in", bool(key) and key != "paste-your-key-here")
except Exception as exc:  # noqa: BLE001
    report(".env readable", False, str(exc))

print("\nAll checks passed." if ok else "\nSome checks failed. See the FAIL lines above.")
sys.exit(0 if ok else 1)
