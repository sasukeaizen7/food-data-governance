-- One row per quality rule: how many products it checked, how many failed, and how much usage they carry.
with results as (
    select r.rule_id, r.status, p.unique_scans
    from {{ ref('dq_results') }} r
    join {{ ref('products') }} p using (code)
),

totals as (
    select count(*) as n_products, sum(unique_scans) as total_scans from {{ ref('products') }}
)

select
    d.rule_id,
    d.rule_name,
    d.dimension,
    d.severity,
    d.owner_role,
    d.description,
    count(r.rule_id)::bigint as n_checked,
    count(*) filter (where r.status = 'fail')::bigint as n_failed,
    round(100.0 * count(*) filter (where r.status = 'fail') / nullif(count(r.rule_id), 0), 2)::double as pct_failed,
    coalesce(sum(r.unique_scans) filter (where r.status = 'fail'), 0)::bigint as scans_on_failed,
    round(100.0 * coalesce(sum(r.unique_scans) filter (where r.status = 'fail'), 0)
          / nullif(any_value(t.total_scans), 0), 2)::double as pct_of_all_scans
from {{ ref('dq_rules') }} d
left join results r using (rule_id)
cross join totals t
group by all
order by d.rule_id
