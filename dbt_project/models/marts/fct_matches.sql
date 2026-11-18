-- Adapted from the paper's code (github.com/igargouri10/football-analytics-platform, MIT licence).
-- Only finished matches. TheSportsDB now reports a finished match as 'FT'; when the
-- paper's data was pulled it was 'Match Finished'. We map 'FT' to 'Match Finished' so the
-- column keeps the paper's values (its tests accept 'Match Finished', not 'FT').
-- Incremental like the original; the paper always ran --full-refresh.
{{
  config(
    materialized='incremental',
    unique_key='match_id'
  )
}}

with stg_matches as (
    select * from {{ ref('stg_matches') }}
)

select
    cast(match_data.idEvent as integer)      as match_id,
    cast(match_data.idLeague as integer)     as league_id,
    cast(match_data.strSeason as varchar)    as season,
    cast(match_data.dateEvent as date)       as match_date,
    case when cast(match_data.strStatus as varchar) = 'FT' then 'Match Finished'
         else cast(match_data.strStatus as varchar) end as match_status,
    cast(match_data.idHomeTeam as integer)   as home_team_id,
    cast(match_data.strHomeTeam as varchar)  as home_team_name,
    cast(match_data.intHomeScore as integer) as home_team_score,
    cast(match_data.idAwayTeam as integer)   as away_team_id,
    cast(match_data.strAwayTeam as varchar)  as away_team_name,
    cast(match_data.intAwayScore as integer) as away_team_score
from stg_matches
where cast(match_data.strStatus as varchar) in ('Match Finished', 'FT')

{% if is_incremental() %}
  and cast(match_data.dateEvent as date) > (select max(match_date) from {{ this }})
{% endif %}
