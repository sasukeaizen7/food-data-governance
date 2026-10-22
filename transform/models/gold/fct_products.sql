-- Products sold in France with their quality status: the table consumers should use.
with issues as (
    select
        code,
        list(rule_id order by rule_id) filter (where status = 'fail' and severity <> 'info') as failed_rules,
        count(*) filter (where status = 'fail' and severity = 'blocking') as n_blocking,
        count(*) filter (where status = 'fail' and severity = 'major') as n_major,
        count(*) filter (where status = 'fail' and severity = 'minor') as n_minor,
        bool_or(rule_id = 'R02' and status = 'fail') as is_internal_code
    from {{ ref('dq_results') }}
    group by code
)

select
    p.code,
    p.product_name,
    p.brand,
    p.brand_key,
    p.category,
    p.categories,
    p.created_at,
    p.last_modified_at,
    p.unique_scans,
    p.nutriscore_grade,
    p.nutriscore_score,
    p.nova_group,
    p.energy_kcal_100g,
    p.fat_100g,
    p.saturated_fat_100g,
    p.carbohydrates_100g,
    p.sugars_100g,
    p.fiber_100g,
    p.proteins_100g,
    p.salt_100g,
    coalesce(i.failed_rules, []) as failed_rules,
    coalesce(i.n_blocking, 0) as n_blocking,
    coalesce(i.n_major, 0) as n_major,
    coalesce(i.n_minor, 0) as n_minor,
    coalesce(i.is_internal_code, false) as is_internal_code,
    case
        when i.n_blocking > 0 then 'untrusted'
        when i.n_major > 0 then 'caution'
        else 'trusted'
    end as trust_level
from {{ ref('products') }} p
left join issues i using (code)
