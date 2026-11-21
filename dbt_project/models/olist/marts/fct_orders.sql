-- One row per sampled order, with item, payment and review totals.
-- Orders without items (for example some cancelled or unavailable orders) have item_count 0.
with orders as (
    select * from {{ ref('stg_olist_orders') }}
),

items as (
    select
        order_id,
        count(*)            as item_count,
        sum(price)          as items_value,
        sum(freight_value)  as freight_value
    from {{ ref('stg_olist_order_items') }}
    group by order_id
),

payments_ranked as (
    select
        *,
        -- 1 = the payment that paid the largest share of the order (first one on ties)
        row_number() over (partition by order_id
                           order by payment_value desc, payment_sequential) as value_rank
    from {{ ref('stg_olist_payments') }}
),

payments as (
    select
        order_id,
        sum(payment_value)                              as payment_value,
        max(payment_installments)                       as payment_installments,
        max(case when value_rank = 1 then payment_type end) as payment_type
    from payments_ranked
    group by order_id
),

reviews_ranked as (
    select
        *,
        row_number() over (partition by order_id order by created_at desc, review_id) as recency_rank
    from {{ ref('stg_olist_reviews') }}
),

reviews as (
    select order_id, review_score
    from reviews_ranked
    where recency_rank = 1
)

select
    o.order_id,
    o.customer_id,
    o.order_status,
    o.purchased_at,
    o.approved_at,
    o.delivered_carrier_at,
    o.delivered_customer_at,
    o.estimated_delivery_date,
    coalesce(i.item_count, 0)                       as item_count,
    coalesce(i.items_value, 0)                      as items_value,
    coalesce(i.freight_value, 0)                    as freight_value,
    p.payment_value,
    p.payment_installments,
    p.payment_type,
    r.review_score,
    date_diff('day', o.purchased_at, o.delivered_customer_at) as delivery_days
from orders o
left join items i on i.order_id = o.order_id
left join payments p on p.order_id = o.order_id
left join reviews r on r.order_id = o.order_id
