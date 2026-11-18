-- Adapted from the paper's code (github.com/igargouri10/football-analytics-platform,
-- MIT licence). One row per event, kept as a single struct column like the original.
-- The original read the JSON straight from S3; we read the raw table loaded from disk.
{{ config(materialized='table') }}

with raw as (
    select * from {{ source('raw', 'epl_season') }}
)

select
    unnest(events) as match_data
from raw
