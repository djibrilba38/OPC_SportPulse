{{ config(materialized='table') }}

select
    employee_id,
    department,
    declared_activity
from {{ source('silver', 'silver_employees') }}