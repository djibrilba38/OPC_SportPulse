{{ config(materialized='table') }}

select distinct
    activity_type,
    case activity_type
        when 'Running' then 'Course à pied'
        when 'Walking' then 'Marche'
        when 'Cycling' then 'Cyclisme'
        else activity_type
    end as activity_type_label
from {{ source('silver', 'silver_activities') }}