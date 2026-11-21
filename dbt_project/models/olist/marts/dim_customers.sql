-- Customers of the sampled orders (in Olist every order has its own customer_id;
-- customer_unique_id identifies the person across orders).
select c.*
from {{ ref('stg_olist_customers') }} c
where c.customer_id in (select customer_id from {{ ref('stg_olist_orders') }})
