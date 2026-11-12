# 0007 · Invented people with an answer key; real public data for places and feeds

*2026-10*

**Context.** Identity resolution, deletion and targeting can only be measured against the truth, and real
consumer data with ground truth is neither available nor appropriate for a public project.

**Decision.** People are generated (with typos, moves, households, namesakes and vendor errors) and every record
keeps a hidden answer key. Buildings and incomes are real (NYC MapPLUTO, Census ACS); two feeds are real and live
(NYC Open Data permits, Wikimedia's edit stream); the cloud warehouse is real.

**Rejected.** Public "customer" datasets: no ground truth for who is who. Scraped personal data: not acceptable.

**Costs.** A generator can be easier than reality: the matcher was tuned on the same generator it is scored on,
so its 0.99 is an upper bound; the "Try your files" page claims no accuracy for uploaded files for that reason.

**Evidence.** `docs/synthetic_data_card.md` (damage measured per kind); identity F1 holds from 20,000 to 300,000
people; a scale-dependent namesake bug was found because the answer key exists.

**Revisit when** real consented data with ground truth is available.
