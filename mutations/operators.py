"""Mutation operators: each one damages a copy of the data in one known way.

Every operator takes an open DuckDB connection to the copy, a Mutation describing what to
damage, and a seeded random generator, and returns how many rows it changed. Rows are
picked by DuckDB's rowid, so a mutation with the same seed always hits the same rows.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

import duckdb


@dataclass
class Mutation:
    operator: str
    table: str
    column: str | None = None
    variant: str = "default"
    rows: int = 1
    seed: int = 1
    parent_table: str | None = None     # orphaned_child only
    parent_column: str | None = None

    @property
    def mutation_id(self) -> str:
        return "__".join([self.operator, self.table, self.column or "_", self.variant,
                          f"r{self.rows}", f"s{self.seed}"])

    def to_dict(self) -> dict:
        return {"mutation_id": self.mutation_id, **asdict(self)}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _pick_rowids(con, table: str, rng: random.Random, k: int, where: str = "true") -> list[int]:
    ids = [r[0] for r in con.execute(f"select rowid from {_q(table)} where {where} order by rowid").fetchall()]
    return rng.sample(ids, min(k, len(ids)))


def _in_list(ids: list[int]) -> str:
    return "(" + ",".join(str(int(i)) for i in ids) + ")"


def null_injection(con, m: Mutation, rng: random.Random) -> int:
    """Set the column to NULL in some rows."""
    ids = _pick_rowids(con, m.table, rng, m.rows, f"{_q(m.column)} is not null")
    if ids:
        con.execute(f"update {_q(m.table)} set {_q(m.column)} = null where rowid in {_in_list(ids)}")
    return len(ids)


def duplicate_key(con, m: Mutation, rng: random.Random) -> int:
    """Insert rows that reuse an existing key but carry another row's other values."""
    keys = [r[0] for r in con.execute(
        f"select {_q(m.column)} from {_q(m.table)} where {_q(m.column)} is not null order by 1").fetchall()]
    donors = [r[0] for r in con.execute(f"select rowid from {_q(m.table)} order by rowid").fetchall()]
    if not keys or len(donors) < 2:
        return 0
    n = 0
    for key in rng.sample(keys, min(m.rows, len(keys))):
        donor = rng.choice(donors)
        con.execute(f"insert into {_q(m.table)} select * replace (? as {_q(m.column)}) "
                    f"from {_q(m.table)} where rowid = ?", [key, donor])
        n += 1
    return n


def invalid_category(con, m: Mutation, rng: random.Random) -> int:
    """Put a value outside the column's valid set.

    variant unknown: a made-up code; variant case: a valid value with its case changed
    (the kind of near-miss a loose join or a manual entry produces).
    """
    ids = _pick_rowids(con, m.table, rng, m.rows, f"{_q(m.column)} is not null")
    if not ids:
        return 0
    if m.variant == "unknown":
        expr = f"'INVALID_{m.seed}'"
    else:  # case
        expr = (f"case when lower({_q(m.column)}) <> {_q(m.column)} then lower({_q(m.column)}) "
                f"else upper({_q(m.column)}) end")
    con.execute(f"update {_q(m.table)} set {_q(m.column)} = {expr} where rowid in {_in_list(ids)}")
    return len(ids)


def out_of_range(con, m: Mutation, rng: random.Random) -> int:
    """Push numbers far outside the observed range (variant high or negative)."""
    lo, hi = con.execute(f"select min({_q(m.column)}), max({_q(m.column)}) from {_q(m.table)}").fetchone()
    if lo is None:
        return 0
    span = (hi - lo) + 1
    value = hi + 10 * span if m.variant == "high" else lo - 10 * span
    ids = _pick_rowids(con, m.table, rng, m.rows, f"{_q(m.column)} is not null")
    if ids:
        con.execute(f"update {_q(m.table)} set {_q(m.column)} = ? where rowid in {_in_list(ids)}", [value])
    return len(ids)


def orphaned_child(con, m: Mutation, rng: random.Random) -> int:
    """Delete parent rows that child rows point to, so those children reference nothing."""
    used = [r[0] for r in con.execute(
        f"select distinct p.{_q(m.parent_column)} from {_q(m.parent_table)} p "
        f"join {_q(m.table)} c on c.{_q(m.column)} = p.{_q(m.parent_column)} order by 1").fetchall()]
    if not used:
        return 0
    victims = rng.sample(used, min(m.rows, len(used)))
    placeholders = ",".join("?" for _ in victims)
    con.execute(f"delete from {_q(m.parent_table)} where {_q(m.parent_column)} in ({placeholders})", victims)
    return len(victims)


def date_shift(con, m: Mutation, rng: random.Random) -> int:
    """Move dates two years forward or backward (temporal drift)."""
    days = 730 if m.variant == "forward" else -730
    ctype = con.execute(f"select typeof({_q(m.column)}) from {_q(m.table)} limit 1").fetchone()[0]
    ids = _pick_rowids(con, m.table, rng, m.rows, f"{_q(m.column)} is not null")
    if ids:
        con.execute(f"update {_q(m.table)} set {_q(m.column)} = "
                    f"cast({_q(m.column)} + interval ({days}) day as {ctype}) where rowid in {_in_list(ids)}")
    return len(ids)


def double_count(con, m: Mutation, rng: random.Random) -> int:
    """Load rows twice: variant partial copies some rows, variant full copies the whole table."""
    if m.variant == "full":
        n = con.execute(f"select count(*) from {_q(m.table)}").fetchone()[0]
        con.execute(f"insert into {_q(m.table)} select * from {_q(m.table)}")
        return n
    total = con.execute(f"select count(*) from {_q(m.table)}").fetchone()[0]
    ids = _pick_rowids(con, m.table, rng, max(1, round(total * 0.1)))
    if ids:
        con.execute(f"insert into {_q(m.table)} select * from {_q(m.table)} where rowid in {_in_list(ids)}")
    return len(ids)


OPERATORS = {
    "null_injection": null_injection,
    "duplicate_key": duplicate_key,
    "invalid_category": invalid_category,
    "out_of_range": out_of_range,
    "orphaned_child": orphaned_child,
    "date_shift": date_shift,
    "double_count": double_count,
}


def apply(con: duckdb.DuckDBPyConnection, m: Mutation) -> int:
    return OPERATORS[m.operator](con, m, random.Random(f"{m.mutation_id}"))
