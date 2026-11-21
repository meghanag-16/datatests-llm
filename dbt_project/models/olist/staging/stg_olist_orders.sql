-- Orders, typed. Only a deterministic sample of orders is kept (var olist_orders, default
-- 5000): the order ids are ranked by their md5 hash, so the same orders are chosen on every
-- machine and every run. Set olist_orders to 0 to keep all orders.
{{ config(materialized='view') }}

with orders as (
    select * from {{ source('olist_raw', 'orders') }}
),

sampled as (
    select order_id
    from orders
    {% if var('olist_orders') | int > 0 %}
    order by md5(order_id)
    limit {{ var('olist_orders') | int }}
    {% endif %}
)

select
    order_id,
    customer_id,
    order_status,
    cast(order_purchase_timestamp as timestamp)      as purchased_at,
    cast(order_approved_at as timestamp)             as approved_at,
    cast(order_delivered_carrier_date as timestamp)  as delivered_carrier_at,
    cast(order_delivered_customer_date as timestamp) as delivered_customer_at,
    cast(order_estimated_delivery_date as date)      as estimated_delivery_date
from orders
where order_id in (select order_id from sampled)
