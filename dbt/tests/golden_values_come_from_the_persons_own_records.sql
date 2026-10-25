-- No value may appear on a golden person unless one of that person's own records stated it.
select p.person_key, 'personal_email' as attr
from {{ ref('dim_person') }} p
where p.personal_email is not null
  and not exists (select 1 from {{ ref('stg_observations') }} o
                  where o.person_key = p.person_key and o.attribute = 'email' and o.value = p.personal_email)
union all
select p.person_key, 'address'
from {{ ref('dim_person') }} p
where p.address_line is not null
  and not exists (select 1 from {{ ref('stg_observations') }} o
                  where o.person_key = p.person_key and o.attribute = 'address'
                    and o.value = p.address_line || '|' || coalesce(p.zip, ''))
