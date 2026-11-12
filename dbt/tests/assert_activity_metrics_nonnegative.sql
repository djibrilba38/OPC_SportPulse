select *
from {{ ref('fact_activity') }}
where duration_min <= 0
   or distance_m < 0