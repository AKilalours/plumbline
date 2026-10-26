# Synthetic data card

Read this before quoting any model result built on this data. Every relationship a model
can learn here was put there on purpose, and this page lists them.

## Real inputs

| Input | Used for | Not used |
| --- | --- | --- |
| NYC PLUTO tax lots: bbl, address, zipcode, borough, unitsres, yearbuilt, bldgclass | where people live; building size; owner-type lots (unitsres <= 2) | owner names, never requested or loaded |
| Census ACS 5-year B19013 median household income by ZCTA | center of each household's income distribution | anything person-level |

## Invented

Names (from Faker's static US name lists), dates of birth, emails, phones, employers, job titles,
households, moves, job changes, home purchases.

## Planted rules (default config)

- Household size 1 to 4 with probabilities 0.32, 0.33, 0.17, 0.18.
- Household income: lognormal around the ZIP's Census median, sigma 0.55.
- Lots are chosen with weight min(residential units, 50).
- Initial owner: lot has <= 2 units and a 60% draw.
- Employment: 85% under age 67, 20% at 67 or older. 15% of the employed change jobs once.
- Home purchase per household: base 4%, multiplied by
  income (exp(1.2 x log income ratio to city median), clipped 0.3 to 3),
  age of head (1.6 if 28 to 45, else 0.6), renting (1.5, owners 0.4),
  and a head's job change (1.5); capped at 0.5. A purchase happens at least 30 days after
  that job change, and moves the household to a <= 2 unit lot as owners.
- Other moves: 12% of households, to a lot weighted by size.

A model that "discovers" income, age, renting and a recent job change as purchase drivers
is rediscovering these rules. Report it that way.

## Planted campaign response (`synth/campaign.py`)

The winter reactivation email of 2026-01-06, by each person's true order history on 2025-12-31.
Chance of buying in the next 30 days without the email, and what the email adds:

| Segment | Without the email | Added by the email |
| --- | --- | --- |
| Never ordered | 0.6% | +0.2 x income band points |
| Lapsed (last order 180+ days before) | 2% | +4.5 points |
| Cooling (90 to 180 days) | 4% | +3 points |
| Active (last 90 days) | 8%, +2 points per order in the past year beyond the first, up to +6 | 0, or -1 point for 4+ orders in the past year |

Each person's effect is multiplied by a hidden lognormal factor (mean 1, sigma 0.6). An email reaches
everyone who reads the inbox. 15% of buyers order twice; 10% of orders use the buyer's other address
and 3% a mistyped one. An uplift model that finds "lapsed customers respond" is rediscovering these
rules. Report it that way.

## Damage

| Kind | CRM | Vendor | Email |
| --- | --- | --- | --- |
| Nickname (first name) | 10% where a nickname exists | same | none |
| Typo (one character, never the first) | 8% first name, 8% last name, 4% email, 4% street | 8% last name, 4% street, 4% email | none |
| Missing | 5% email, 5% phone, 2.5% ZIP | 5% email, 5% phone, 5% owner flag, 5% date of birth | none |
| Format | 4 phone formats; title-case address; 30% street abbreviations | 2 name formats, 3 date formats, uppercase address | 5% uppercase email |
| Stale address after a move | 70% | 40% | n/a |
| Duplicates | 3% of customers sign up twice | 5% of each week repeats earlier rows | 2% duplicated rows on the corrected day |
| Noisy attributes | none | income band 65% exact, 25% off by one, 10% random; owner flag 85% correct | none |

Every damaged field is recorded in `truth/damage_log`.

## Known simplifications

- All people are adults; no children.
- A shared household inbox (5% of non-head members) means a deletion request for that
  email hash also matches the other household member. Real systems face this too.
- Moves and purchases are household-wide; only one move or purchase per household.
- Generation held 1.2 GB at 300,000 people, so 1 million should need about 4 GB; that size is
  not measured, and chunked generation is not built.
