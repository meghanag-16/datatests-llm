{#
  Column test: every non-null value lies within [min_value, max_value].
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
    {% if min_value is not none %} or {{ column_name }} < {{ min_value }} {% endif %}
    {% if max_value is not none %} or {{ column_name }} > {{ max_value }} {% endif %}
  )
{% endtest %}
