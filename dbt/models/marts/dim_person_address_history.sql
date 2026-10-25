-- Point in time: where each person lived, as far as the sources tell us. Each address starts the
-- day it was first vouched for and ends when a newer address appears. "Where did this person live
-- on 2025-06-30" is a range lookup, the through-time view Minerva sells.
with a as (
    select person_key, value, first_observed_at, last_observed_at, best_source as source
    from {{ ref('int_attribute_values') }}
    where attr = 'address'
),
-- an echo of an older address after a newer one appeared does not reopen it
ordered as (
    select *, row_number() over (partition by person_key order by first_observed_at, value) as seq
    from a
)
select person_key, seq,
       split_part(value, '|', 1) as address_line,
       nullif(split_part(value, '|', 2), '') as zip,
       source as first_source,
       first_observed_at as valid_from,
       lead(first_observed_at) over (partition by person_key order by seq) as valid_to,
       last_observed_at
from ordered
