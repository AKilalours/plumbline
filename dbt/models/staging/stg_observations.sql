-- Every fact any source states about a person, one row per (record, attribute), with the time the
-- source last vouched for it. Names and addresses come standardized from identity_records, so the
-- same street spelled two ways counts as one value.
with ir as (
    select * from {{ source('wh', 'identity_records') }}
),

addr as (
    select unique_id,
           case when house is not null and street is not null
                then house || ' ' || street || coalesce(' APT ' || unit, '') end as address_line
    from ir
),

-- When did the vendor last re-send this exact row? A weekly re-send is a fresh confirmation.
vendor_confirmed as (
    select v.vendor_record_id, max(l.delivered_at) as confirmed_at
    from {{ source('wh', 'current_vendor') }} v
    join {{ source('wh', 'delivery_rows') }} d on d.source = 'vendor' and d.row_hash = v._row_hash
    join {{ source('wh', 'ingest_ledger') }} l on l.delivery_id = d.delivery_id and l.status = 'loaded'
    group by 1
),

records as (
    select ir.person_key, 'crm' as source, ir.record_id, cast(c.updated_at as timestamp) as observed_at,
           cast(ir.first_name as varchar) as first_name, cast(ir.last_name as varchar) as last_name, cast(ir.email as varchar) as email, cast(ir.phone as varchar) as phone, a.address_line, cast(ir.zip as varchar) as zip, cast(ir.dob as varchar) as dob,
           null::varchar as employer, null::varchar as job_title, null::varchar as income_band, null::varchar as homeowner
    from ir join addr a using (unique_id)
    join {{ source('wh', 'current_crm') }} c on c.crm_id = ir.record_id
    where ir.source = 'crm'
    union all
    select ir.person_key, 'vendor', ir.record_id, vc.confirmed_at,
           ir.first_name, ir.last_name, ir.email, ir.phone, a.address_line, ir.zip, ir.dob,
           v.employer, v.job_title, cast(v.est_income_band as varchar), cast(v.homeowner_flag as varchar)
    from ir join addr a using (unique_id)
    join {{ source('wh', 'current_vendor') }} v on v.vendor_record_id = ir.record_id
    left join vendor_confirmed vc on vc.vendor_record_id = ir.record_id
    where ir.source = 'vendor'
    union all
    -- a store profile is observed once per order, at the order's time
    select ir.person_key, 'store', o.order_id, o.created_at,
           ir.first_name, ir.last_name, ir.email, null, a.address_line, ir.zip, null,
           null, null, null, null
    from ir join addr a using (unique_id)
    join {{ source('wh', 'identity_store_profiles') }} p on 'store:' || p.profile_id = ir.unique_id
    join {{ source('wh', 'current_store') }} o on o.order_id = p.order_id
    where ir.source = 'store'
),

-- street and zip travel together: an address is one fact, not two that could come from different records
with_address as (
    select *, address_line || '|' || coalesce(zip, '') as address from records
),

long as (
    unpivot with_address
    on first_name, last_name, email, phone, address, dob, employer, job_title, income_band, homeowner
    into name attribute value value
)

select person_key, source, record_id, observed_at, attribute, value from long
union all
-- email events vouch for the address they were sent to, nothing else
select m.person_key, 'email', e.event_id, e.event_ts, 'email', e.email
from {{ source('wh', 'current_email') }} e
join {{ source('wh', 'identity_map') }} m on m.source = 'email' and m.record_id = e.event_id
where m.person_key is not null
