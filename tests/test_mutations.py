import duckdb
import pytest

from mutations.catalog import build_catalog
from mutations.operators import Mutation, apply
from mutations.plan import plan_mutations

CONFIG = {
    "tables": {"parent": {"primary_key": "id"}, "child": {"primary_key": "cid"}},
    "foreign_keys": [{"child": "child.pid", "parent": "parent.id"}],
    "rows_per_mutation": [1],
    "seeds": [1],
}


@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("create table parent as select range::int as id, 'name_' || range as name from range(10)")
    c.execute("""create table child as
                 select range::int as cid, (range % 10)::int as pid,
                        case when range % 2 = 0 then 'A' else 'B' end as status,
                        (range % 5)::int as score, date '2024-01-01' + range::int as day
                 from range(40)""")
    yield c
    c.close()


def test_catalog_roles(con):
    cat = build_catalog(con, CONFIG)
    roles = {f"{c.table}.{c.name}": c.role for c in cat.columns}
    assert roles["parent.id"] == "key"
    assert roles["child.pid"] == "foreign"
    assert roles["child.status"] == "category"
    assert roles["child.score"] == "numeric"
    assert roles["child.day"] == "date"


def test_plan_covers_all_operators(con):
    ops = {m.operator for m in plan_mutations(build_catalog(con, CONFIG))}
    assert ops == {"null_injection", "duplicate_key", "invalid_category", "out_of_range",
                   "orphaned_child", "date_shift", "double_count"}


def test_null_injection(con):
    assert apply(con, Mutation("null_injection", "child", "status", rows=3)) == 3
    assert con.execute("select count(*) from child where status is null").fetchone()[0] == 3


def test_duplicate_key(con):
    apply(con, Mutation("duplicate_key", "parent", "id", rows=2))
    assert con.execute("select count(*) - count(distinct id) from parent").fetchone()[0] == 2


def test_invalid_category_variants(con):
    apply(con, Mutation("invalid_category", "child", "status", variant="unknown", rows=1))
    apply(con, Mutation("invalid_category", "child", "status", variant="case", rows=1, seed=2))
    bad = con.execute("select count(*) from child where status not in ('A', 'B')").fetchone()[0]
    assert bad == 2


def test_out_of_range(con):
    apply(con, Mutation("out_of_range", "child", "score", variant="negative", rows=1))
    assert con.execute("select min(score) from child").fetchone()[0] < 0


def test_orphaned_child(con):
    apply(con, Mutation("orphaned_child", "child", "pid", rows=1, parent_table="parent", parent_column="id"))
    orphans = con.execute("select count(*) from child where pid not in (select id from parent)").fetchone()[0]
    assert orphans == 4


def test_date_shift_keeps_type(con):
    apply(con, Mutation("date_shift", "child", "day", variant="forward", rows=1))
    assert con.execute("select typeof(day) from child limit 1").fetchone()[0] == "DATE"
    assert con.execute("select max(day) from child").fetchone()[0].year == 2026


def test_double_count_full(con):
    apply(con, Mutation("double_count", "child", None, variant="full"))
    assert con.execute("select count(*) from child").fetchone()[0] == 80


def test_same_seed_same_rows():
    def run():
        c = duckdb.connect()
        c.execute("create table t as select range::int as x from range(100)")
        apply(c, Mutation("null_injection", "t", "x", rows=5, seed=7))
        return [r[0] for r in c.execute("select rowid from t where x is null order by 1").fetchall()]
    assert run() == run()
