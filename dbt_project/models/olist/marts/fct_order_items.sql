-- One row per item of a sampled order. order_item_key is a single-column key
-- (order id and item number), so key tests and duplicate-key damage work on one column.
select
    order_id || '-' || cast(order_item_id as varchar) as order_item_key,
    order_id,
    order_item_id,
    product_id,
    seller_id,
    shipping_limit_at,
    price,
    freight_value
from {{ ref('stg_olist_order_items') }}
