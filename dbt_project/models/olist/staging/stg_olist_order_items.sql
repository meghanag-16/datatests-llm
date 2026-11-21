{{ config(materialized='view') }}

select
    i.order_id,
    cast(i.order_item_id as integer)            as order_item_id,
    i.product_id,
    i.seller_id,
    cast(i.shipping_limit_date as timestamp)    as shipping_limit_at,
    cast(i.price as decimal(12, 2))             as price,
    cast(i.freight_value as decimal(12, 2))     as freight_value
from {{ source('olist_raw', 'order_items') }} i
where i.order_id in (select order_id from {{ ref('stg_olist_orders') }})
