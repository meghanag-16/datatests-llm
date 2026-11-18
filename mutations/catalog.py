"""Describe a clean database: tables, column types and the role of each column.

Roles decide which mutation operators apply to a column:
  key       primary key from the config           -> nulls, duplicate key
  foreign   child side of a foreign key            -> nulls, orphaned rows
  category  low-cardinality text column            -> nulls, invalid category
  numeric   number that is not an id               -> nulls, out of range
  date      DATE or TIMESTAMP column               -> nulls, date shift
  other     anything else                          -> nulls
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import yaml

NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "REAL", "DOUBLE", "DECIMAL")
DATES = ("DATE", "TIMESTAMP")


@dataclass
class ColumnInfo:
    table: str
    name: str
    type: str
    role: str
    distinct_values: list = field(default_factory=list)   # filled for categories
    min_value: object = None
    max_value: object = None


@dataclass
class ForeignKey:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


@dataclass
class Catalog:
    tables: dict[str, int]                     # table -> row count
    columns: list[ColumnInfo]
    primary_keys: dict[str, str]
    foreign_keys: list[ForeignKey]
    config: dict

    def by_role(self, role: str) -> list[ColumnInfo]:
        return [c for c in self.columns if c.role == role]


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_catalog(con: duckdb.DuckDBPyConnection, config: dict) -> Catalog:
    tables_cfg = config.get("tables") or {}
    pks = {t: (cfg or {}).get("primary_key") for t, cfg in tables_cfg.items()}
    fks = []
    for fk in config.get("foreign_keys") or []:
        ct, cc = fk["child"].split(".")
        pt, pc = fk["parent"].split(".")
        fks.append(ForeignKey(ct, cc, pt, pc))
    fk_cols = {(f.child_table, f.child_column) for f in fks}
    excluded = set(config.get("exclude_columns") or [])
    max_distinct = int(config.get("category_max_distinct", 25))
    max_ratio = float(config.get("category_max_ratio", 0.5))

    tables, columns = {}, []
    for table in tables_cfg:
        n = con.execute(f'select count(*) from "{table}"').fetchone()[0]
        tables[table] = n
        for name, ctype, *_ in con.execute(f'describe "{table}"').fetchall():
            if f"{table}.{name}" in excluded:
                continue
            utype = ctype.upper()
            info = ColumnInfo(table, name, ctype, "other")
            if pks.get(table) == name:
                info.role = "key"
            elif (table, name) in fk_cols:
                info.role = "foreign"
            elif utype.startswith(DATES):
                info.role = "date"
            elif utype.startswith(NUMERIC) and not name.lower().endswith("_id"):
                info.role = "numeric"
            elif utype.startswith("VARCHAR"):
                distinct = con.execute(f'select count(distinct "{name}") from "{table}"').fetchone()[0]
                if distinct <= max_distinct and n and distinct / n <= max_ratio:
                    info.role = "category"
                    info.distinct_values = [r[0] for r in con.execute(
                        f'select distinct "{name}" from "{table}" where "{name}" is not null order by 1').fetchall()]
            if info.role in ("numeric", "date"):
                info.min_value, info.max_value = con.execute(
                    f'select min("{name}"), max("{name}") from "{table}"').fetchone()
            columns.append(info)
    return Catalog(tables=tables, columns=columns, primary_keys={k: v for k, v in pks.items() if v},
                   foreign_keys=fks, config=config)
