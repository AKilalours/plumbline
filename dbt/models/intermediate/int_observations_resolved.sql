-- Observations with every address spelling replaced by its canonical spelling.
select o.person_key, o.source, o.record_id, o.observed_at, o.attr, o.trust_rank,
       coalesce(c.canonical, o.value) as value, o.value as value_as_sent
from {{ ref('int_observations_typed') }} o
left join {{ ref('int_address_canonical') }} c
  on o.attr = 'address' and c.person_key = o.person_key and c.value = o.value
