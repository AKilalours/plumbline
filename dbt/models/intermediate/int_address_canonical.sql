-- One person's address arrives in several spellings: "ROGERS AVE" and "ROGERSLAVENUE", "APT 18" and
-- "APT P18", a zip present or missing. Counted separately they split the evidence for the real
-- address and look like moves. Here every spelling maps to one canonical spelling: variants share a
-- house number, agree on zip (or one has none) and have street lines at least 0.9 alike (Jaro-
-- Winkler). The canonical form is the spelling seen most often, preferring one with a zip.
-- Known cost: a move between two flats of one building with similar unit numbers is merged.
--
-- "Alike" is not transitive: A can be like B and B like C while A is not like C. Picking each
-- spelling's best look-alike then makes B the canonical of A while B's own canonical is C, so a
-- canonical spelling is not canonical. It happened in no run up to 300,000 people and broke the
-- 1,000,000-person build. Each pointer is now followed to its end (A to B to C): every step moves to
-- a strictly better spelling (zip, then observations, then the value itself), so the chase ends,
-- and where there is no chain nothing changes.
with recursive v as (
    select person_key, value, count(*) as observations,
           split_part(value, '|', 1) as line,
           split_part(value, '|', 2) as zip,
           split_part(split_part(value, '|', 1), ' ', 1) as house
    from {{ ref('int_observations_typed') }}
    where attr = 'address'
    group by 1, 2
),
candidates as (
    select x.person_key, x.value, y.value as canonical,
           row_number() over (partition by x.person_key, x.value
                              order by (y.zip <> '') desc, y.observations desc, y.value) as rn
    from v x
    join v y
      on x.person_key = y.person_key
     and x.house = y.house
     and (x.zip = y.zip or x.zip = '' or y.zip = '')
     and jaro_winkler_similarity(x.line, y.line) >= 0.9
),
pick as (
    select person_key, value, canonical from candidates where rn = 1
),
chase as (
    select person_key, value, canonical, 0 as depth from pick
    union all
    select c.person_key, c.value, n.canonical, c.depth + 1
    from chase c
    join pick n on n.person_key = c.person_key and n.value = c.canonical
    where n.canonical <> c.canonical and c.depth < 50
)
select person_key, value, arg_max(canonical, depth) as canonical
from chase
group by 1, 2
