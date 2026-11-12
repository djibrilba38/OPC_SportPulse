{{ config(materialized='table') }}

with activities as (

    select
        activity_id,
        employee_id,
        activity_type,
        started_at,
        started_at::date as activity_date,
        duration_min,
        distance_m,
        source_run_id
    from {{ source('silver', 'silver_activities') }}

)

select
    a.activity_id,
    a.employee_id,
    a.activity_type,
    d.date_key,
    a.started_at,
    a.duration_min,
    a.distance_m,
    a.source_run_id

from activities as a

inner join {{ ref('dim_employee') }} as e
    on a.employee_id = e.employee_id

inner join {{ ref('dim_activity_type') }} as t
    on a.activity_type = t.activity_type

inner join {{ ref('dim_date') }} as d
    on a.activity_date = d.full_date