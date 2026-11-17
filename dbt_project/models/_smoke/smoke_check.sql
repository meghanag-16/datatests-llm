-- Smoke test: proves dbt can build a table in DuckDB.
-- Safe to remove once real models exist.
select 1 as id, 'dbt and duckdb are working' as message
