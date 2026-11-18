import duckdb
import pytest

from llm_tests.context import Column, TableContext
from llm_tests.validate import Rejected, TestSpec, parse_yaml, to_yaml, validate_reply

CONTEXTS = {
    "teams": TableContext("teams", 2, [Column("team_id", "INTEGER"), Column("name", "VARCHAR")]),
    "matches": TableContext("matches", 2, [Column("match_id", "INTEGER"), Column("home_id", "INTEGER"),
                                           Column("score", "INTEGER"), Column("status", "VARCHAR")]),
}


@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("create table teams as select 1 as team_id, 'A' as name")
    c.execute("create table matches as select 1 as match_id, 1 as home_id, 2 as score, 'FT' as status")
    yield c
    c.close()


REPLY = """Some text before.
```yaml
version: 2
models:
  - name: matches
    data_tests:
      - expression_is_true:
          arguments: {expression: "score >= 0"}
      - expression_is_true:
          arguments: {expression: "no_such_column > 0"}
    columns:
      - name: match_id
        tests: [unique, not_null, not_null]
      - name: home_id
        data_tests:
          - relationships: {to: "ref('teams')", field: team_id}
          - relationships: {to: "ref('players')", field: id}
      - name: status
        data_tests:
          - accepted_values: {values: [FT]}
          - in_range: {min_value: 0}
      - name: missing_col
        data_tests: [not_null]
      - name: score
        data_tests:
          - dbt_utils.at_least_one
```"""


def test_validate_reply_keeps_and_rejects(con):
    kept, rejected = validate_reply(REPLY, "matches", CONTEXTS, con)
    kept_types = sorted(s.type for s in kept)
    assert kept_types == ["accepted_values", "expression_is_true", "not_null", "relationships", "unique"]
    reasons = " | ".join(r.reason for r in rejected)
    assert "does not run" in reasons
    assert "exact duplicate" in reasons
    assert "not a known model" in reasons
    assert "neither numeric nor a date" in reasons
    assert "unknown column" in reasons
    assert "not allowed" in reasons


def test_test_id_is_stable():
    a = TestSpec("matches", "status", "accepted_values", {"values": ["FT"]})
    b = TestSpec("matches", "status", "accepted_values", {"values": ["FT"]})
    assert a.test_id == b.test_id and a.test_id.startswith("matches.status.accepted_values.")


def test_yaml_round_trip(con):
    kept, _ = validate_reply(REPLY, "matches", CONTEXTS, con)
    text = to_yaml(kept, ["teams", "matches"])
    models = parse_yaml(text)
    assert models[0]["name"] == "matches"
    again, rejected = validate_reply(text, "matches", CONTEXTS, con)
    assert not rejected
    assert {s.test_id for s in again} == {s.test_id for s in kept}


def test_bad_yaml_raises():
    with pytest.raises(ValueError):
        parse_yaml("```yaml\nmodels: [\n```")


def test_in_range_accepts_date_bounds(con):
    contexts = {"games": TableContext("games", 1, [Column("day", "DATE"), Column("label", "VARCHAR")])}
    con.execute("create table games as select date '2024-01-05' as day, 'x' as label")
    reply = """```yaml
models:
  - name: games
    columns:
      - name: day
        data_tests:
          - in_range: {min_value: 2023-08-01, max_value: '2024-06-30'}
          - in_range: {min_value: not-a-date}
      - name: label
        data_tests:
          - in_range: {min_value: 0}
```"""
    kept, rejected = validate_reply(reply, "games", contexts, con)
    assert [s.args for s in kept] == [{"min_value": "2023-08-01", "max_value": "2024-06-30"}]
    reasons = " | ".join(r.reason for r in rejected)
    assert "not a date" in reasons and "neither numeric nor a date" in reasons
