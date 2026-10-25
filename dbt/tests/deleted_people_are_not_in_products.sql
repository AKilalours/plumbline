-- Every golden person must trace to records that still exist after deletion.
select p.person_key
from {{ ref('dim_person') }} p
where not exists (select 1 from {{ source('wh', 'identity_map') }} m where m.person_key = p.person_key)
