{{ config(materialized='view') }}

select
    customer_id,
    customer_unique_id,
    lpad(customer_zip_code_prefix, 5, '0')      as zip_code_prefix,
    customer_city                               as city,
    customer_state                              as state
from {{ source('olist_raw', 'customers') }}
