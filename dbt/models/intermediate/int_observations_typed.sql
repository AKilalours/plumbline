-- Observations known by the as_of date, with emails split into personal and work (the B2B2C link)
-- and each source's trust rank for tie-breaks.
select o.person_key, o.source, o.record_id, o.observed_at, o.value,
       case when o.attribute = 'email' then
                case when split_part(o.value, '@', 2) in (select domain from {{ ref('personal_email_domains') }})
                     then 'personal_email' else 'work_email' end
            else o.attribute end as attr,
       coalesce(t.rank, 9) as trust_rank
from {{ ref('stg_observations') }} o
left join {{ ref('source_trust') }} t
  on t.source = o.source
 and t.attribute = case when o.attribute in ('first_name', 'last_name') then 'name' else o.attribute end
where o.observed_at < {{ as_of_end() }}
