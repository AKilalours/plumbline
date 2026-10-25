-- People at one current address. A household is where marketing actually lands (one catalog per door).
select household_key, any_value(address_line) as address_line, any_value(zip) as zip,
       count(*) as members, list(person_key order by person_key) as person_keys,
       sum(revenue) as household_revenue
from {{ ref('dim_person') }}
where address_line is not null
group by 1
