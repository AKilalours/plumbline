-- Point-in-time features for propensity models: everything here is known by the as_of date and
-- nothing after it (every input is filtered by as_of upstream or here). Build it for several
-- as_of dates and you get a training set that cannot see the future.
-- One row per person whose address is known. Windowed features use feature_window_days, which
-- must fit inside the history every snapshot has, so a feature means the same thing at every cutoff.
with p as (
    select * from {{ ref('dim_person') }} where address_line is not null
),
addr as (
    select person_key,
           split_part(address_line, ' ', 1) as house,
           regexp_replace(substr(address_line, length(split_part(address_line, ' ', 1)) + 2), ' APT .*$', '') as street,
           zip,
           address_line like '% APT %' as has_unit
    from p
),
prop as (
    -- property data: the building the person lives in (PLUTO)
    select a.person_key, a.has_unit, r.unitsres, r.yearbuilt, r.bldgclass, r.borough
    from addr a left join {{ source('wh', 'ref_property') }} r using (house, street, zip)
),
orders as (
    select m.person_key,
           count(*) as orders_window,
           -- Money is summed as exact cents. Summed as floating point, the result depended on the order
           -- DuckDB's threads added the prices in, so two machines disagreed in the last bit and the
           -- model's data fingerprint (its version) differed for identical data.
           coalesce(sum(cast(o.total_price as decimal(14, 2))) filter (where o.financial_status = 'paid' or o.updated_at >= {{ as_of_end() }}), 0) as revenue_window,
           date_diff('day', max(o.created_at), {{ as_of_end() }}) as days_since_last_order_in_window
    from {{ source('wh', 'current_store') }} o
    join {{ source('wh', 'identity_map') }} m on m.source = 'store_order' and m.record_id = o.order_id
    where o.created_at < {{ as_of_end() }} and o.created_at >= {{ as_of_end() }} - interval {{ var('feature_window_days') }} day
    group by 1
),
moves as (
    select person_key,
           count(*) filter (where detected_at >= {{ as_of_end() }} - interval {{ var('feature_window_days') }} day) as moves_window
    from {{ ref('fct_life_events') }}
    where confidence in ('high', 'medium')
    group by 1
),
work as (
    -- a person showing a new work email is a person who changed jobs
    select person_key,
           count(distinct value) as work_emails_seen,
           max(first_observed_at) as newest_work_email_seen_at
    from {{ ref('int_attribute_values') }}
    where attr = 'work_email'
    group by 1
)
select
    p.person_key,
    cast('{{ var("as_of") }}' as date) as as_of,
    ln(nullif(z.median_income, 0)) as log_zip_income,
    pr.unitsres,
    ln(1 + pr.unitsres) as log_units,
    case when pr.unitsres is null then null else pr.unitsres <= 2 end as small_building,
    left(pr.bldgclass, 1) as bldg_family,
    pr.yearbuilt,
    pr.has_unit,
    pr.unitsres is not null as property_matched,
    coalesce(o.orders_window, 0) as orders_window,
    coalesce(o.revenue_window, 0) as revenue_window,
    p.revenue / nullif(p.orders - p.refunds, 0) as avg_order_value,
    o.days_since_last_order_in_window,
    p.opens_90d, p.clicks_90d,
    coalesce(mv.moves_window, 0) as moves_window,
    coalesce(w.work_emails_seen, 0) as work_emails_seen,
    w.newest_work_email_seen_at >= {{ as_of_end() }} - interval {{ var('feature_window_days') }} day and coalesce(w.work_emails_seen, 0) >= 2 as new_job_window,
    -- third-party (vendor) attributes, only once the vendor had delivered them by as_of
    p.income_band,
    date_diff('year', try_cast(p.dob as date), {{ as_of_end() }}) as age_years,
    p.homeowner as vendor_homeowner,
    -- known from the brand's own systems, not only from a third party
    list_has_any(p.sources, ['crm', 'store']) as first_party
from p
left join prop pr using (person_key)
left join {{ source('wh', 'ref_zip_income') }} z on z.zip = p.zip
left join orders o using (person_key)
left join moves mv using (person_key)
left join work w using (person_key)
