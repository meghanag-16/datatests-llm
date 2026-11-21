-- Products sold in the sampled orders.
select p.*
from {{ ref('stg_olist_products') }} p
where p.product_id in (select product_id from {{ ref('stg_olist_order_items') }})
