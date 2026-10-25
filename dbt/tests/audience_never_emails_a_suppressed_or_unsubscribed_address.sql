-- Compliance: rows returned here would be emails sent to someone who asked to be deleted, shares an
-- inbox with such a person, or unsubscribed. Must be empty.
select a.person_key, p.personal_email
from {{ ref('mart_activation_audience') }} a
join {{ ref('dim_person') }} p using (person_key)
where a.email_ok
  and (sha256(p.personal_email) in (select email_sha256 from {{ source('wh', 'suppression') }} where email_sha256 is not null)
       or p.personal_email in (select email from {{ source('wh', 'current_email') }} where event_type = 'unsubscribe'))
