-- Survivorship: which value is the person's value today, per attribute.
-- Most attributes: the value vouched for most recently wins; ties go to the more trusted source.
-- Address is different. Stale sources keep echoing an old address after a move (the CRM keeps it
-- unless the customer edits it), so "most recently mentioned" picks the old home. A newer address
-- supersedes an older one: the address first seen most recently wins, among addresses still being
-- confirmed within stale_after_days.
with v as (
    select * from {{ ref('int_attribute_values') }}
),
ranked as (
    select v.*,
           row_number() over (
               partition by person_key, attr
               order by
                   case when attr = 'address'
                        then case when last_observed_at >= {{ as_of_end() }} - interval {{ var("stale_after_days") }} day
                                  then first_observed_at end end desc nulls last,
                   last_observed_at desc, best_trust_rank, observations desc, value
           ) as rn,
           count(*) over (partition by person_key, attr) as distinct_values,
           sum(observations) over (partition by person_key, attr) as all_observations
    from v
)
select person_key, attr, value, first_observed_at, last_observed_at, best_source as source,
       distinct_values, round(observations / all_observations, 3) as agreement
from ranked where rn = 1
