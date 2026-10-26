-- The golden person: one row per resolved person, every value with where and when it came from.
with w as (select * from {{ ref('int_attribute_winners') }}),
p as (
    select person_key,
        max(value) filter (where attr = 'first_name') as first_name,
        max(value) filter (where attr = 'last_name') as last_name,
        max(value) filter (where attr = 'personal_email') as personal_email,
        max(value) filter (where attr = 'work_email') as work_email,
        max(value) filter (where attr = 'phone') as phone,
        max(split_part(value, '|', 1)) filter (where attr = 'address') as address_line,
        max(nullif(split_part(value, '|', 2), '')) filter (where attr = 'address') as zip,
        max(source) filter (where attr = 'address') as address_source,
        max(last_observed_at) filter (where attr = 'address') as address_confirmed_at,
        max(agreement) filter (where attr = 'address') as address_agreement,
        max(value) filter (where attr = 'dob') as dob,
        max(value) filter (where attr = 'employer') as employer,
        max(value) filter (where attr = 'job_title') as job_title,
        max(try_cast(value as int)) filter (where attr = 'income_band') as income_band,
        max(value = 'true') filter (where attr = 'homeowner') as homeowner,
        count(*) filter (where distinct_values > 1) as attributes_in_conflict
    from w group by person_key
),
recs as (
    -- counted from what was known by as_of, not from today's identity graph: a buyer who moves
    -- gets a new store profile at the new address, so an all-time count leaks the outcome
    select person_key, count(distinct source || ':' || record_id) as records,
           list(distinct source order by source) as sources
    from {{ ref('int_observations_typed') }}
    where source in ('crm', 'vendor', 'store')
    group by 1
),
orders as (
    select m.person_key,
           count(*) as orders,
           -- an order refunded after as_of was still paid on as_of
           sum(o.total_price) filter (where o.financial_status = 'paid' or o.updated_at >= {{ as_of_end() }}) as revenue,
           count(*) filter (where o.financial_status = 'refunded' and o.updated_at < {{ as_of_end() }}) as refunds,
           min(o.created_at) as first_order_at,
           max(o.created_at) as last_order_at
    from {{ source('wh', 'current_store') }} o
    join {{ source('wh', 'identity_map') }} m on m.source = 'store_order' and m.record_id = o.order_id
    where o.created_at < {{ as_of_end() }}
    group by 1
),
email as (
    select m.person_key,
           count(*) filter (where e.event_type = 'open' and e.event_ts > {{ as_of_end() }} - interval 90 day) as opens_90d,
           count(*) filter (where e.event_type = 'click' and e.event_ts > {{ as_of_end() }} - interval 90 day) as clicks_90d,
           list(distinct e.email) filter (where e.event_type = 'unsubscribe') as unsubscribed_emails
    from {{ source('wh', 'current_email') }} e
    join {{ source('wh', 'identity_map') }} m on m.source = 'email' and m.record_id = e.event_id
    where m.person_key is not null and e.event_ts < {{ as_of_end() }}
    group by 1
)
select p.*,
       p.address_confirmed_at < {{ as_of_end() }} - interval {{ var("stale_after_days") }} day as address_is_stale,
       p.personal_email is not null and p.work_email is not null as b2b2c_linked,
       r.records, r.sources,
       coalesce(o.orders, 0) as orders, coalesce(o.revenue, 0) as revenue, coalesce(o.refunds, 0) as refunds,
       o.first_order_at, o.last_order_at,
       coalesce(e.opens_90d, 0) as opens_90d, coalesce(e.clicks_90d, 0) as clicks_90d,
       coalesce(e.unsubscribed_emails, []) as unsubscribed_emails,
       md5(p.address_line || '|' || coalesce(p.zip, '')) as household_key
from p
join recs r using (person_key)
left join orders o using (person_key)
left join email e using (person_key)
