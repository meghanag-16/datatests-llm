{#
  Column test: every non-null value lies within [min_value, max_value].
  Works on numbers and on dates (bounds written as 'YYYY-MM-DD').
  Either bound may be left out. Usage:
    - in_range:
        arguments:
          min_value: 0
          max_value: 20
#}
{% test in_range(model, column_name, min_value=none, max_value=none) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and (
    false
    {% if min_value is not none %} or {{ column_name }} < {% if min_value is string %}'{{ min_value }}'{% else %}{{ min_value }}{% endif %} {% endif %}
    {% if max_value is not none %} or {{ column_name }} > {% if max_value is string %}'{{ max_value }}'{% else %}{{ max_value }}{% endif %} {% endif %}
  )
{% endtest %}
