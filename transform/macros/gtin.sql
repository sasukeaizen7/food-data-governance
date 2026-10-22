{# True when `code` is a GTIN (8, 12, 13 or 14 digits) with a correct GS1 check digit.
   Weights alternate 3, 1, 3, ... starting from the digit just left of the check digit. #}
{% macro gtin_is_valid(code) -%}
(
    regexp_full_match({{ code }}, '[0-9]{8}|[0-9]{12,14}')
    and (
        10 - list_sum([
            (ascii(substr({{ code }}, i, 1)) - 48)
            * case when (length({{ code }}) - i) % 2 = 1 then 3 else 1 end
            for i in range(1, length({{ code }}))
        ]) % 10
    ) % 10 = ascii(substr({{ code }}, length({{ code }}), 1)) - 48
)
{%- endmacro %}

{# In-store codes: GS1 restricted-circulation prefixes 02x, 04x and 2xx (on the 13-digit form). #}
{% macro gtin_is_internal(code) -%}
(
    length({{ code }}) in (12, 13)
    and regexp_matches(lpad({{ code }}, 13, '0'), '^(02|04|2)')
)
{%- endmacro %}
