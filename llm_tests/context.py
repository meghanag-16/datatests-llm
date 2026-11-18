"""Collect what the LLM sees about each table: columns, types, row count and sample rows."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb


@dataclass
class Column:
    name: str
    type: str


@dataclass
class TableContext:
    name: str
    row_count: int
    columns: list[Column]
    sample_header: list[str] = field(default_factory=list)
    sample_rows: list[tuple] = field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def sample_as_csv(self) -> str:
        def cell(v) -> str:
            if v is None:
                return "NULL"
            text = str(v)
            return f'"{text}"' if ("," in text or '"' in text) else text
        lines = [",".join(self.sample_header)]
        lines += [",".join(cell(v) for v in row) for row in self.sample_rows]
        return "\n".join(lines)


def table_context(con: duckdb.DuckDBPyConnection, table: str, sample_rows: int = 10,
                  seed: int = 42) -> TableContext:
    cols = [Column(name=r[0], type=r[1]) for r in con.execute(f'describe "{table}"').fetchall()]
    n = con.execute(f'select count(*) from "{table}"').fetchone()[0]
    # Deterministic sample: order rows by a seeded hash of all their values, so the same
    # data always gives the same prompt (and the response cache keeps working).
    # Floating-point columns are rounded first: parallel aggregation can change the last digits.
    exprs = [f'round("{c.name}", 6) as "{c.name}"' if c.type.upper() in ("DOUBLE", "FLOAT", "REAL")
             else f'"{c.name}"' for c in cols]
    names = ", ".join(f'"{c.name}"' for c in cols)
    cur = con.execute(
        f'select * from (select {", ".join(exprs)} from "{table}") '
        f'order by hash({int(seed)}, {names}) limit {int(sample_rows)}'
    )
    header = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return TableContext(name=table, row_count=n, columns=cols, sample_header=header, sample_rows=rows)


def load_contexts(db_path: Path, tables: list[str], sample_rows: int = 10, seed: int = 42) -> dict[str, TableContext]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {t: table_context(con, t, sample_rows, seed) for t in tables}
    finally:
        con.close()
