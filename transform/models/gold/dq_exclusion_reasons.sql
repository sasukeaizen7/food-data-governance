-- Why products are not trusted: one row per blocking or major rule, counted in products.
-- A product can fail several rules, so the rows do not add up to the number of excluded
-- products; that total is in the first row (rule_id = 'ALL').
with excluded as (
    select code, failed_rules from {{ ref('fct_products') }} where trust_level <> 'trusted'
),

total as (
    select count(*) as n from excluded
)

select
    'ALL' as rule_id,
    'any blocking or major rule' as rule_name,
    null as severity,
    null as owner_role,
    (select n from total) as excluded_products,
    100.0 as pct_of_excluded

union all

select
    s.rule_id,
    s.rule_name,
    s.severity,
    s.owner_role,
    count(distinct e.code) as excluded_products,
    round(100.0 * count(distinct e.code) / any_value(t.n), 1) as pct_of_excluded
from excluded e, unnest(e.failed_rules) as f(rule_id)
join {{ ref('dq_rule_summary') }} s using (rule_id)
cross join total t
where s.severity in ('blocking', 'major')
group by s.rule_id, s.rule_name, s.severity, s.owner_role
