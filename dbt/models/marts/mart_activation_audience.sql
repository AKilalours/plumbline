-- Who may be contacted, on which channel, and in which segment. This is what leaves the building,
-- so the rules here are compliance rules, not preferences. Attributes and segments are as of the
-- as_of date; consent is always current: an audience rebuilt for a past date must still honour an
-- unsubscribe or a deletion that came later.
--   email: never to an address on the deletion/suppression list (a shared family inbox included,
--          even though the family member's data was kept) and never after an unsubscribe (CAN-SPAM)
--   phone: never to a suppressed number
--   mail:  only to an address confirmed within stale_after_days
with unsubscribed as (
        -- an unsubscribe belongs to the address, whoever it was attributed to (a shared inbox is
        -- attributed to nobody, and must still be honoured). No as_of filter: consent is current.
        select distinct email from {{ source('wh', 'current_email') }}
        where event_type = 'unsubscribe'
     ),
     s_email as (select email_sha256 as h from {{ source('wh', 'suppression') }} where email_sha256 is not null),
     s_phone as (select phone_sha256 as h from {{ source('wh', 'suppression') }} where phone_sha256 is not null),
     p as (select * from {{ ref('dim_person') }}),
     cutoff as (
        select quantile_cont(revenue, 0.9) as top_decile
        from p where orders > 0
     )
select p.person_key,
    p.first_name, p.last_name,
    p.personal_email is not null
        and sha256(p.personal_email) not in (select h from s_email)
        and p.personal_email not in (select email from unsubscribed) as email_ok,
    p.work_email is not null
        and sha256(p.work_email) not in (select h from s_email)
        and p.work_email not in (select email from unsubscribed) as work_email_ok,
    p.phone is not null and sha256(p.phone) not in (select h from s_phone) as phone_ok,
    p.address_line is not null and not coalesce(p.address_is_stale, true) as mail_ok,
    p.orders > 0 as is_customer,
    p.orders > 0 and p.revenue >= (select top_decile from cutoff) as high_value,
    p.orders > 0 and p.last_order_at < {{ as_of_end() }} - interval 180 day as lapsed,
    exists (select 1 from {{ ref('fct_life_events') }} e
            where e.person_key = p.person_key and e.confidence in ('high', 'medium')
              and e.detected_at > {{ as_of_end() }} - interval 90 day) as recent_mover,
    p.b2b2c_linked,
    p.income_band, p.homeowner
from p
