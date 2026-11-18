{#
  Model test: every row satisfies a SQL boolean expression (rows where it is null pass). Usage (model level):
    data_tests:
      - expression_is_true:
          arguments:
            expression: "home_team_id <> away_team_id"
#}
{% test expression_is_true(model, expression) %}
select *
from {{ model }}
where not ({{ expression }})
{% endtest %}
