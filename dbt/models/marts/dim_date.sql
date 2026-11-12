{{ config(materialized='table') }}

with activity_dates as (

    select distinct
        started_at::date as full_date
    from {{ source('silver', 'silver_activities') }}

)

select
    cast(to_char(full_date, 'YYYYMMDD') as integer) as date_key,
    full_date,
    extract(year from full_date)::integer as year,
    extract(quarter from full_date)::integer as quarter,
    extract(month from full_date)::integer as month,
    to_char(full_date, 'FMMonth') as month_name,
    extract(day from full_date)::integer as day_of_month,
    extract(isodow from full_date)::integer as day_of_week,
    to_char(full_date, 'FMDay') as day_name,
    extract(isodow from full_date) in (6, 7) as is_weekend
from activity_dates