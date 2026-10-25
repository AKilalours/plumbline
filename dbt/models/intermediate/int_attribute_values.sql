-- One row per (person, attribute, distinct value): when it was first and last vouched for, by how
-- many records and sources. Survivorship and history both read from here.
select person_key, attr, value,
       min(observed_at) as first_observed_at,
       max(observed_at) as last_observed_at,
       count(*) as observations,
       count(distinct source) as sources,
       min(trust_rank) as best_trust_rank,
       arg_min(source, trust_rank) as best_source
from {{ ref('int_observations_resolved') }}
group by all
