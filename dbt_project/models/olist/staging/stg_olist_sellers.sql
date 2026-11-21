{{ config(materialized='view') }}

select
    seller_id,
    lpad(seller_zip_code_prefix, 5, '0')        as zip_code_prefix,
    seller_city                                 as city,
    seller_state                                as state
from {{ source('olist_raw', 'sellers') }}
