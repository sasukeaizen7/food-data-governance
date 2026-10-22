-- Data quality scorecard per brand (brands with at least `min_brand_products` products).
with by_brand as (
    select
        brand_key,
        mode(brand) as brand,
        count(*) as n_products,
        sum(unique_scans) as scans,
        count(*) filter (where trust_level = 'trusted') as n_trusted,
        count(*) filter (where trust_level = 'caution') as n_caution,
        count(*) filter (where trust_level = 'untrusted') as n_untrusted
    from {{ ref('fct_products') }}
    where brand_key is not null
    group by brand_key
    having count(*) >= {{ var('min_brand_products') }}
),

top_issue as (
    select brand_key, rule_id, count(*) as n
    from {{ ref('fct_products') }}, unnest(failed_rules) as t(rule_id)
    where brand_key is not null
    group by brand_key, rule_id
    qualify row_number() over (partition by brand_key order by n desc, rule_id) = 1
)

select
    b.brand_key,
    b.brand,
    b.n_products,
    b.scans,
    round(100.0 * b.n_trusted / b.n_products, 1) as pct_trusted,
    round(100.0 * b.n_caution / b.n_products, 1) as pct_caution,
    round(100.0 * b.n_untrusted / b.n_products, 1) as pct_untrusted,
    t.rule_id as top_issue_rule,
    t.n as top_issue_products
from by_brand b
left join top_issue t using (brand_key)
