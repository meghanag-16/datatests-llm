"""Turn a catalog into the full list of mutations to create."""
from __future__ import annotations

from .catalog import Catalog
from .operators import Mutation


def plan_mutations(cat: Catalog, operators: list[str] | None = None) -> list[Mutation]:
    rows = list(cat.config.get("rows_per_mutation") or [1, 5])
    seeds = list(cat.config.get("seeds") or [1, 2, 3])
    out: list[Mutation] = []

    def each(op: str, table: str, column: str | None, variants=("default",), **extra):
        for variant in variants:
            for r in rows:
                for s in seeds:
                    out.append(Mutation(op, table, column, variant, r, s, **extra))

    for c in cat.columns:
        each("null_injection", c.table, c.name)
    for table, pk in cat.primary_keys.items():
        each("duplicate_key", table, pk)
    for c in cat.by_role("category"):
        each("invalid_category", c.table, c.name, ("unknown", "case"))
    for c in cat.by_role("numeric"):
        each("out_of_range", c.table, c.name, ("high", "negative"))
    for fk in cat.foreign_keys:
        each("orphaned_child", fk.child_table, fk.child_column,
             parent_table=fk.parent_table, parent_column=fk.parent_column)
    for c in cat.by_role("date"):
        each("date_shift", c.table, c.name, ("forward", "backward"))
    for table in cat.tables:
        for s in seeds:
            out.append(Mutation("double_count", table, None, "partial", 0, s))
        out.append(Mutation("double_count", table, None, "full", 0, 1))

    if operators:
        out = [m for m in out if m.operator in operators]
    return out
