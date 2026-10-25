-- Detected moves: a person shows up at a new address. Minerva's thesis is that life events drive
-- spending; this is the evidence-based version, dated by when the new address was first vouched for.
--
-- Confidence comes from the evidence. A store order proves someone received a parcel there on that
-- date; a CRM edit is the customer's own change; a vendor row is dated by when the file arrived,
-- not by when anything happened, so a vendor sending an old address late looks like a move back.
-- Measured precision by evidence (seed 7): store 86%, CRM 79%, vendor only 45%. In production these
-- rates would come from a labelled audit sample; segments use high and medium only.
select person_key, 'move' as event_type, valid_from as detected_at,
       lag(address_line) over (partition by person_key order by seq) as from_address,
       address_line as to_address, zip as to_zip, first_source as evidence_source,
       case first_source when 'store' then 'high' when 'crm' then 'medium' else 'low' end as confidence
from {{ ref('dim_person_address_history') }}
qualify seq > 1
