-- Reviews, typed. review_id is not unique in the source and an order can have more than one
-- review; fct_orders keeps the latest one per order.
{{ config(materialized='view') }}

select
    r.review_id,
    r.order_id,
    cast(r.review_score as integer)             as review_score,
    cast(r.review_creation_date as timestamp)   as created_at,
    cast(r.review_answer_timestamp as timestamp) as answered_at
from {{ source('olist_raw', 'order_reviews') }} r
where r.order_id in (select order_id from {{ ref('stg_olist_orders') }})
