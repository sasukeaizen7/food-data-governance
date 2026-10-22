-- One row per (product, rule) the rule applies to, with the outcome.
-- A rule only applies when the data it needs is there: a product without sugars
-- is not "passing" the sugars check, it is simply not checked by it.
{% set stale_years = var('stale_after_years') %}

with p as (
    select * from {{ ref('products') }}
),

duplicates as (
    select ltrim(code, '0') as short_code
    from p
    group by 1
    having count(*) > 1
),

checks as (
    select code, 'R01' as rule_id, true as applies,
        not {{ gtin_is_valid('code') }} as failed,
        code as observed
    from p

    union all
    select code, 'R02', {{ gtin_is_valid('code') }},
        {{ gtin_is_internal('code') }},
        code
    from p

    union all
    select p.code, 'R03', true, d.short_code is not null, p.code
    from p left join duplicates d on ltrim(p.code, '0') = d.short_code

    union all
    select code, 'R04', true, product_name is null, null
    from p

    union all
    select code, 'R05', product_name is not null,
        regexp_matches(product_name, '\x{FFFD}|Ã[©¨ª«®´¹¼§¢ ]|â€'),
        product_name
    from p

    union all
    select code, 'R06', true, brand is null, null
    from p

    union all
    select code, 'R07', true, category is null, null
    from p

    union all
    select code, 'R08', not no_nutrition_data and not nutrition_label_exempt,
        energy_kcal_100g is null and energy_kj_100g is null and fat_100g is null
            and carbohydrates_100g is null and proteins_100g is null,
        null
    from p

    union all
    select code, 'R09',
        coalesce(energy_kcal_100g, energy_kj_100g, fat_100g, saturated_fat_100g, carbohydrates_100g,
                 sugars_100g, fiber_100g, proteins_100g, salt_100g) is not null,
        coalesce(energy_kcal_100g > 900 or energy_kj_100g > 3800, false)
            or exists (
                select 1 from unnest([fat_100g, saturated_fat_100g, carbohydrates_100g, sugars_100g,
                                      fiber_100g, proteins_100g, salt_100g, energy_kcal_100g, energy_kj_100g]) t(v)
                where v < 0
            )
            or exists (
                select 1 from unnest([fat_100g, saturated_fat_100g, carbohydrates_100g, sugars_100g,
                                      fiber_100g, proteins_100g, salt_100g]) t(v)
                where v > 100
            ),
        concat_ws(' | ', 'kcal=' || energy_kcal_100g, 'fat=' || fat_100g, 'carbs=' || carbohydrates_100g,
                  'sugars=' || sugars_100g, 'proteins=' || proteins_100g, 'salt=' || salt_100g)
    from p

    union all
    select code, 'R10', sugars_100g is not null and carbohydrates_100g is not null,
        sugars_100g > carbohydrates_100g + 0.5,
        'sugars=' || sugars_100g || ' carbs=' || carbohydrates_100g
    from p

    union all
    select code, 'R11', saturated_fat_100g is not null and fat_100g is not null,
        saturated_fat_100g > fat_100g + 0.5,
        'saturated=' || saturated_fat_100g || ' fat=' || fat_100g
    from p

    union all
    select code, 'R12', fat_100g is not null and carbohydrates_100g is not null and proteins_100g is not null,
        fat_100g + carbohydrates_100g + proteins_100g + coalesce(fiber_100g, 0) + coalesce(salt_100g, 0) > 105,
        'sum=' || round(fat_100g + carbohydrates_100g + proteins_100g + coalesce(fiber_100g, 0) + coalesce(salt_100g, 0), 1)
    from p

    union all
    select code, 'R13', applies, failed,
        'declared=' || round(energy_kcal_100g) || ' computed=' || round(computed)
    from (
        select *,
            energy_kcal_100g is not null and fat_100g is not null
                and carbohydrates_100g is not null and proteins_100g is not null as applies,
            -- EU factors: fat 9, carbs 4, protein 4, fibre 2, polyols 2.4 (counted in carbs at 4),
            -- alcohol 7 kcal/g with 0.789 g per % vol
            9 * fat_100g + 4 * carbohydrates_100g + 4 * proteins_100g + 2 * coalesce(fiber_100g, 0)
                - 1.6 * coalesce(polyols_100g, 0) + 5.5 * coalesce(alcohol_100g, 0) as computed,
            abs(energy_kcal_100g - computed) > 20
                and abs(energy_kcal_100g - computed) > 0.2 * greatest(energy_kcal_100g, computed) as failed
        from p
    )

    union all
    -- Labels compute kJ and kcal separately from each nutrient (EU factors: fat 37/9, carbs 17/4,
    -- fibre 8/2...), so a correct ratio lies between 4.0 and 4.25, not exactly 4.184. Rounding and
    -- older labels widen that; outside 3.5-5.0 one of the two values is wrong (often kJ typed as kcal).
    select code, 'R14', energy_kcal_100g > 0 and energy_kj_100g > 0,
        (energy_kj_100g < 3.5 * energy_kcal_100g or energy_kj_100g > 5.0 * energy_kcal_100g)
            and abs(energy_kj_100g - 4.184 * energy_kcal_100g) > 10,
        'kj=' || energy_kj_100g || ' kcal=' || energy_kcal_100g
    from p

    union all
    select code, 'R15', salt_100g is not null and sodium_100g is not null,
        abs(salt_100g - 2.5 * sodium_100g) > greatest(0.05, 0.1 * salt_100g),
        'salt=' || salt_100g || ' sodium=' || sodium_100g
    from p

    union all
    select code, 'R17',
        (mfr_energy_kcal_100g is not null and pkg_energy_kcal_100g is not null)
            or (mfr_sugars_100g is not null and pkg_sugars_100g is not null)
            or (mfr_fat_100g is not null and pkg_fat_100g is not null)
            or (mfr_salt_100g is not null and pkg_salt_100g is not null),
        coalesce(abs(mfr_energy_kcal_100g - pkg_energy_kcal_100g) > greatest(10, 0.1 * mfr_energy_kcal_100g), false)
            or coalesce(abs(mfr_sugars_100g - pkg_sugars_100g) > greatest(1, 0.1 * mfr_sugars_100g), false)
            or coalesce(abs(mfr_fat_100g - pkg_fat_100g) > greatest(1, 0.1 * mfr_fat_100g), false)
            or coalesce(abs(mfr_salt_100g - pkg_salt_100g) > greatest(0.1, 0.1 * mfr_salt_100g), false),
        concat_ws(' | ',
            'kcal ' || mfr_energy_kcal_100g || ' vs ' || pkg_energy_kcal_100g,
            'sugars ' || mfr_sugars_100g || ' vs ' || pkg_sugars_100g,
            'fat ' || mfr_fat_100g || ' vs ' || pkg_fat_100g,
            'salt ' || mfr_salt_100g || ' vs ' || pkg_salt_100g) || ' (manufacturer vs packaging)'
    from p

    union all
    select code, 'R16', last_modified_at is not null,
        last_modified_at < current_date - interval {{ stale_years }} year,
        cast(last_modified_at as date)::varchar
    from p
)

select
    c.code,
    c.rule_id,
    r.severity,
    r.dimension,
    case when c.failed then 'fail' else 'pass' end as status,
    case when c.failed then c.observed end as observed
from checks c
join {{ ref('dq_rules') }} r using (rule_id)
where c.applies
