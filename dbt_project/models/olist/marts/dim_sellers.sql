-- Sellers of the sampled orders.
select s.*
from {{ ref('stg_olist_sellers') }} s
where s.seller_id in (select seller_id from {{ ref('stg_olist_order_items') }})
