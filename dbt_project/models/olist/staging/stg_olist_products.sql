-- Products, typed. The source spells two column names "lenght"; they are renamed here.
-- The category is translated to English where a translation exists.
{{ config(materialized='view') }}

select
    p.product_id,
    coalesce(t.product_category_name_english, p.product_category_name) as category,
    cast(p.product_name_lenght as integer)        as name_length,
    cast(p.product_description_lenght as integer) as description_length,
    cast(p.product_photos_qty as integer)         as photos_qty,
    cast(p.product_weight_g as integer)           as weight_g,
    cast(p.product_length_cm as integer)          as length_cm,
    cast(p.product_height_cm as integer)          as height_cm,
    cast(p.product_width_cm as integer)           as width_cm
from {{ source('olist_raw', 'products') }} p
left join {{ source('olist_raw', 'category_translation') }} t
    on t.product_category_name = p.product_category_name
