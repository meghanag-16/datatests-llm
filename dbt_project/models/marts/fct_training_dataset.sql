-- Adapted from the paper's code (github.com/igargouri10/football-analytics-platform, MIT licence).
-- The original had no materialization config and the project default was a view, so this
-- model is a view: damage injected into fct_matches shows up here too, which the paper's
-- anomaly scoring relies on (e.g. a null match_id is also caught on this model).
{{ config(materialized='view') }}

with matches as (
    select
        match_id,
        match_date,
        home_team_id,
        home_team_name,
        away_team_id,
        away_team_name,
        home_team_score,
        away_team_score,
        case
            when home_team_score > away_team_score then 'HOME_WIN'
            when away_team_score > home_team_score then 'AWAY_WIN'
            else 'DRAW'
        end as match_result
    from {{ ref('fct_matches') }}
),

-- one row per team per match
team_matches as (
    select match_id, match_date, home_team_id as team_id,
           home_team_score as goals_scored, away_team_score as goals_conceded
    from matches
    union all
    select match_id, match_date, away_team_id as team_id,
           away_team_score as goals_scored, home_team_score as goals_conceded
    from matches
),

-- averages over the previous 5 games, excluding the current one
team_rolling_features as (
    select
        team_id,
        match_date,
        avg(goals_scored) over (
            partition by team_id order by match_date
            rows between 5 preceding and 1 preceding
        ) as avg_goals_scored_last_5,
        avg(goals_conceded) over (
            partition by team_id order by match_date
            rows between 5 preceding and 1 preceding
        ) as avg_goals_conceded_last_5
    from team_matches
),

team_features as (
    select
        team_id,
        match_date,
        max(avg_goals_scored_last_5)   as avg_goals_scored_last_5,
        max(avg_goals_conceded_last_5) as avg_goals_conceded_last_5
    from team_rolling_features
    group by 1, 2
)

select
    m.match_id,
    m.match_date,
    m.match_result,
    m.home_team_id,
    m.home_team_name,
    coalesce(hf.avg_goals_scored_last_5, 0)   as home_avg_goals_scored_last_5,
    coalesce(hf.avg_goals_conceded_last_5, 0) as home_avg_goals_conceded_last_5,
    m.away_team_id,
    m.away_team_name,
    coalesce(af.avg_goals_scored_last_5, 0)   as away_avg_goals_scored_last_5,
    coalesce(af.avg_goals_conceded_last_5, 0) as away_avg_goals_conceded_last_5
from matches m
left join team_features hf on m.home_team_id = hf.team_id and m.match_date = hf.match_date
left join team_features af on m.away_team_id = af.team_id and m.match_date = af.match_date
