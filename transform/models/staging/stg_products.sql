-- Latest version of every product: the full export plus any newer daily delta.
with unioned as (
    select *, 'full' as loaded_from, 2 as source_rank
    from {{ source('bronze', 'products_full') }}
    union all by name
    select * exclude (delta_file), 'delta' as loaded_from, 1 as source_rank
    from {{ source('bronze', 'products_delta') }}
)

select * exclude (source_rank)
from unioned
where code is not null and trim(code) <> ''
qualify row_number() over (
    partition by code
    order by last_modified_t desc nulls last, source_rank
) = 1
