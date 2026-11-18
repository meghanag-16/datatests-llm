-- Adapted from the paper's code (github.com/igargouri10/football-analytics-platform, MIT licence).
{{ config(materialized='table') }}

with stg_matches as (
    select * from {{ ref('stg_matches') }}
),

home_teams as (
    select
        cast(match_data.idHomeTeam as integer)  as team_id,
        cast(match_data.strHomeTeam as varchar) as team_name
    from stg_matches
),

away_teams as (
    select
        cast(match_data.idAwayTeam as integer)  as team_id,
        cast(match_data.strAwayTeam as varchar) as team_name
    from stg_matches
),

all_teams as (
    select * from home_teams
    union all
    select * from away_teams
)

select distinct
    team_id,
    team_name
from all_teams
order by team_name
