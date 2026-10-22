-- One row per product sold in France, cleaned and typed.
select
    trim(code) as code,
    nullif(trim(product_name), '') as product_name,
    -- first declared brand; brands_tags is normalised, brands keeps the original spelling
    nullif(trim(split_part(brands, ',', 1)), '') as brand,
    -- the daily files do not normalise tags ('xx:Bjorg' vs 'bjorg'): lower-case, dashes for spaces
    nullif(lower(regexp_replace(regexp_replace(brands_tags[1], '^[a-z]{2}:', ''), '\s+', '-', 'g')), '') as brand_key,
    -- categories_tags lists the whole hierarchy; the last tag is the most specific
    nullif(regexp_replace(categories_tags[-1], '^[a-z]{2}:', ''), '') as category,
    categories_tags as categories,
    to_timestamp(created_t) as created_at,
    to_timestamp(last_modified_t) as last_modified_at,
    coalesce(unique_scans_n, 0) as unique_scans,
    popularity_key,
    completeness,
    lower(nutriscore_grade) as nutriscore_grade,
    nutriscore_score,
    nova_group,
    coalesce(no_nutrition_data, false) as no_nutrition_data,
    -- EU regulation 1169/2011 (art. 16 and annex V) exempts these from the nutrition declaration
    list_has_any(categories_tags, [
        'en:waters', 'en:alcoholic-beverages', 'en:fresh-fruits', 'en:fresh-vegetables',
        'en:spices', 'en:herbs', 'en:salts', 'en:teas', 'en:herbal-teas', 'en:coffees',
        'en:vinegars', 'en:honeys', 'en:chewing-gum'
    ]) as nutrition_label_exempt,
    energy_kcal_100g,
    energy_kj_100g,
    fat_100g,
    saturated_fat_100g,
    carbohydrates_100g,
    sugars_100g,
    fiber_100g,
    proteins_100g,
    salt_100g,
    sodium_100g,
    alcohol_100g,
    polyols_100g,
    -- what the manufacturer and the packaging (typed in by a contributor) each declare;
    -- only known for products that came through a daily delta file
    mfr_energy_kcal_100g,
    mfr_fat_100g,
    mfr_sugars_100g,
    mfr_salt_100g,
    pkg_energy_kcal_100g,
    pkg_fat_100g,
    pkg_sugars_100g,
    pkg_salt_100g,
    off_quality_errors,
    off_quality_warnings,
    loaded_from
from {{ ref('stg_products') }}
where list_contains(countries_tags, 'en:france')
  and not coalesce(obsolete, false)
