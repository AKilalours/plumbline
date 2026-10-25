-- Each raw spelling must map to exactly one canonical address, and canonical spellings must map to themselves.
select person_key, value from {{ ref('int_address_canonical') }} group by 1, 2 having count(*) > 1
union all
select c.person_key, c.canonical from {{ ref('int_address_canonical') }} c
join {{ ref('int_address_canonical') }} k on k.person_key = c.person_key and k.value = c.canonical
where k.canonical <> k.value
