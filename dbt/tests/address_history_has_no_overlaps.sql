-- Point-in-time lookups need exactly one address per person per day: intervals must not overlap,
-- and only the last one may be open-ended.
select person_key
from {{ ref('dim_person_address_history') }}
group by person_key
having count(*) filter (where valid_to is null) <> 1
    or count(*) filter (where valid_to is not null and valid_to < valid_from) > 0
