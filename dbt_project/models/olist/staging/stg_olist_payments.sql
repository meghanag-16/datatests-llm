{{ config(materialized='view') }}

select
    p.order_id,
    cast(p.payment_sequential as integer)       as payment_sequential,
    p.payment_type,
    cast(p.payment_installments as integer)     as payment_installments,
    cast(p.payment_value as decimal(12, 2))     as payment_value
from {{ source('olist_raw', 'order_payments') }} p
where p.order_id in (select order_id from {{ ref('stg_olist_orders') }})
