{# The moment "as of <date>" means: the end of that day. Comparing timestamps to the bare date
   (midnight) once silently dropped every event on the as-of day, including 15 unsubscribes. #}
{% macro as_of_end() -%}
  (cast('{{ var("as_of") }}' as date) + interval 1 day)
{%- endmacro %}
