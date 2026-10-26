# Plumbline

[![tests](https://github.com/AKilalours/plumbline/actions/workflows/tests.yml/badge.svg)](https://github.com/AKilalours/plumbline/actions/workflows/tests.yml)

**A consumer data platform where every value has a receipt.**

100,000 invented New Yorkers, with a hidden answer key, reach a fictional outdoor brand through
four messy feeds: a CRM, a third-party data vendor, email events and an online store API that
throttles, crashes and changes data mid-sync. Plumbline loads them, works out which records are the
same person, and deletes people who ask to be deleted, everywhere. Every number below is measured
against the answer key, and the tests that hold each one are named.

**At a glance** (seed 7, 100,000 people, 314,579 rows, one command; time and memory per stage at 20,000 to 300,000 people are in [Scale](#scale)):

| | Result |
| --- | --- |
| Ingestion | 0 invariant violations; 100 injected faults change nothing; a crashed API sync resumes without loss |
| Identity resolution | F1 **0.991** (precision 99.4%, recall 98.8%) vs 0.927 for exact email-or-phone matching, and it holds at 3x the population (0.990 at 300,000 people), where the first version fell to 0.960 |
| Person-level deletion | deleted people with data left: 23 → **11** of 500; records of people who never asked, removed: 69 → **50** |
| Golden person (dbt) | current address right for 90.9% of people, against a ceiling of 91.8% (the rest moved and no source saw it); work and personal identities linked 99.3% right |
| Home-buyer model | AUC 0.661 and 2.3x lift in the top decile on a later, untouched period; the best possible is 0.806; a leaky version scoring 0.837 is caught because it beats the best possible |
| Income estimate | for people the vendor never covered: within one band of the true income 83% of the time, against 79.2% for ZIP income alone; the vendor's own band, where it exists, gets 94.7% |
| Campaign experiment | a holdout test of a reactivation email: +1.31 points of 30-day buyers measured, +1.47 (95% CI +1.16 to +1.77) after correcting for orders nobody can attribute, and the answer key's true +1.61 falls inside only the corrected interval; an uplift model sends the next email to the 30% it changes most, for 38 extra buyers per 1,000 emails against 24 for "the likeliest buyers" (best possible: 43) |
| Home-seller model | **not published**: validation AUC 0.47, and even the rule that generated the data reaches only 0.56; the guardrails refused it, as they should |
| Operations agent | answers from the team's docs with a citation on every claim (on 67 eval questions: 19 of 24 right on the held-out half, the right section in the top 3 for 44 of 49, and 7 of 9 unanswerable ones refused); reads live state read-only; changes nothing until a second person with the right role approves; found and, after approval, fixed a stale runbook passage |
| Access and questionnaires | roles granted, removed and offboarded only by an admin who neither asked nor is the person; every change recorded with who approved it and through which request; security questionnaires drafted from the controls page with a source per answer, and controls we do not have left for a person |
| Tests | 188 Python tests and 27 dbt tests, each a stated promise; deliberate breaks show which ones bite |

| Phase | What | Status |
| --- | --- | --- |
| 0 | Synthetic world and answer key | Done |
| 1 | Module A: ingestion, including the store API connector | Done |
| 2 | Module B: identity resolution, person-level deletion, data products (dbt) | Done |
| 3 | Module C: home-buyer model with point-in-time features and publishing guardrails | Done |
| 4 | Module E: operations agent (knowledge with sources, read-only tools, approvals, audit, Slack) | Done |
| 5 | Income estimate, home-seller assessment, access provisioning, security questionnaires, hours retired | Done |
| 6 | CI, campaign experiment and uplift model, Iceberg lake, scale runs, bigger evals, Docker | Done |

## Receipts

| Claim | Measured | Where it is tested |
| --- | --- | --- |
| Re-running ingestion changes nothing | 29 of 29 files skipped, 0 rows added | `test_rerun_is_a_no_op` |
| Injected faults never corrupt current data | 100 random faults (re-sends, partial and reformatted re-sends, truncated files, renamed and extra columns, bad values): current tables identical to the clean baseline | `test_hundred_random_faults_change_nothing_they_should_not` (1,500-person run) |
| Deleted people stay deleted, even when old files are replayed | 0 rows in storage or quarantine match a deletion request | `test_replayed_old_files_cannot_resurrect_deleted_people` |
| A rebuild from the raw files is exact | identical current tables and quarantine | `test_rebuild_from_scratch_is_identical` |
| Seed 7, 100,000 people, real reference data | 253,172 rows from 29 files in 11.9 s; 0 invariant violations | run in a cloud workspace |
| The store API sync is exact under faults | 61,399 orders over 246 pages through 63 failures (31 throttles, 16 server errors, 16 half-sent bodies) and 15 refunds during the sync; warehouse holds exactly 61,036 (all orders minus 363 of deleted customers), 0 duplicates | `test_faults_and_mid_sync_refunds_still_converge` |
| A crashed sync resumes without loss or a full re-pull | resumes from the saved cursor; the next sync fetches only changes | `test_crash_between_pages_resumes_without_loss`, `test_second_sync_only_fetches_changes` |
| A change in the same second as the watermark is not lost | the 5-minute overlap re-reads it; the loader drops the repeats | `test_change_in_the_same_second_as_the_watermark_is_not_lost` |
| The API token never reaches disk | not in landing files, sync state, warehouse or the client's repr | `test_token_never_written_anywhere` |

**What the tests are worth:** I broke six promises in the loader one at a time (no version
replacement, no row dedup, two deletion checks, no missing-column rejection, no file ledger).
Four were caught at once. One exposed a missing test (a corrected file that retracts rows), which
is now added and catches it. One survived because the check was redundant, so it was removed.
The same for the store connector: of five breaks (ignore Retry-After, retry a 401, ignore resume
state, drop the overlap, take the watermark from the last page only), three were caught at once.
Dropping the overlap survived, which exposed the missing same-second test, now added. The last
one changes nothing (pages are sorted, so the last page always holds the newest time).

## Identity resolution (Module B)

Which CRM rows, vendor rows and store customers are the same real person, scored against the hidden
answer key (seed 7, 100,000 people, 122,994 records; the key is used only to score, never to train):

| | Precision | Recall | F1 | People resolved exactly |
| --- | --- | --- | --- | --- |
| Exact email or phone (the obvious baseline) | 95.6% | 89.9% | 0.927 | 91.5% |
| Plumbline: Splink + cannot-link clustering | **99.4%** | **98.8%** | **0.991** | **98.9%** |

Recall by damage type (share of a record's true partners it was joined with): nickname 91.6%,
name typo 96.2%, missing zip 96.5%, missing phone 97.3%, missing email 97.4%, address typo 97.8%,
email typo 97.9%, undamaged 99.3%. The baseline gets 68 to 72% on missing or misspelled contact fields.
The match threshold, 0.9, was fixed before scoring.
Email events, which carry only an address, attach to a person 92.3% of the time and are right 99.7%
of the time; an address shared by a family is left unattached on purpose.

What it took, in the order the problems showed up:

1. **Households look like matches.** The first model had 32% precision: training on blocks of
   "same last name and zip" taught EM that a family is a match. Training on blocks where families
   cannot dominate (same phone; same first and last name) fixed the weights.
2. **Usernames repeat the name.** `brandonanderson@` at two providers scored +13 bits, counting the
   name twice. The email comparison dropped its "same username" level.
3. **Chains.** Connected components joined two spouses through a record with no first name.
   Clustering now merges strongest-first and refuses any merge that puts two clearly different first
   names, or two birth years, in one person (cannot-link). Mixed-up clusters fell from 12,065 to 515.
4. **Non-determinism.** With refusals, merge order matters; DuckDB returns ties in a different order
   each run, and cluster names depended on row order. Both are now total orders, with a regression test.

The matcher never sees how the data was damaged: its nickname and street-suffix tables are written
independently of the generator (a test fails if it imports the generator or reads the answer key).
Its nickname list covers 38 of the generator's 40; Tina for Christina was not added after the scores
showed it missing, because that would be tuning to the answer key.

5. **Namesakes multiply with the population.** Precision was 99.1% at 20,000 people, 97.4% at
   100,000 and 93.5% at 300,000: a model tuned at one size got quietly worse at the next. Of the
   pairs it wrongly joined at 300,000, 89% were linked by "email within 2 edits", against 3% of right
   pairs. Addresses here are built from names, so two namesakes differ by a separator or a digit
   (`scott.williams` / `scott_williams`, `ryan.green36` / `ryan.green32`), and Fellegi-Sunter, which
   treats each field as independent evidence, counted the name a second time. Its weight was learned
   from random pairs, which almost never share a name, so it was far too high for pairs that do. A
   typo now has to be one edit, at the same provider, that changes a letter. Precision went to 99.4%
   at 100,000 and 99.3% at 300,000, recall unchanged; email-typo recall fell from 98.2% to 97.9%.
   Mixed-up clusters went from 556 to 144 at 100,000 and from 3,589 to 418 at 300,000.

6. **Some people cannot be told apart at all.** Scoring asserted that every store profile (one email,
   name and ship-to address) is one person. It held up to 300,000 people and crashed the 1,000,000
   run: 10 of 280,271 profiles belong to two people with one name, one home and one inbox (two Bryan
   Daltons at one address). No data the store has separates them, so the scorer now gives such a
   profile to its main buyer and charges the matcher for the other, and reports the count. The guard
   still fails if people who share a profile differ in name or household, which is what a wrong
   profile key would look like.

Remaining error class: family members (137 of the 144 mixed-up clusters are one household).

## Deletion at the person level

| | Deleted people with any record left | Records of people who never asked, removed |
| --- | --- | --- |
| Phase 1: delete rows matching the request's email or phone | 23 of 500 | 69 |
| Phase 2: one erasure rule + tombstones + identity graph | **11 of 500** | **50** (34 are email events of the listed address) |

**A bug Phase 1 never measured.** Phase 1 tests checked that nothing of a deleted person remained, never
that nothing of anyone else was lost. Measured, hash deletion removed 69 records of family members who
share an inbox with the requester. The data showed a rule with no cost: a row that matches the email
but carries a different phone of its own was never the requester (20 rows, 0 requester rows), so it is
kept. Phone matches never hit anyone else. One rule (`ingest/erasure.py`) now decides for load, purge,
the current views, quarantine and the invariant checks, so they cannot disagree.

**Tombstones.** A purged row leaves only HMAC-keyed hashes of name + zip, last name + address and name
+ date of birth, tagged with its request and how strong the evidence was (phone matched: strong; email
only: weak). After identity resolution, a surviving record matching two key kinds of a trusted tombstone
is deleted along with its whole person, and its email and phone join suppression so replays stay
deleted (`test_person_level_deletions_survive_a_full_replay`). A weak tombstone counts only if its name
agrees with a strong one from the same request: otherwise a housemate's own phone-less order would
mark the housemate for deletion (this exact bug deleted 12 innocent records before the rule).
A known date of birth that disagrees vetoes the match (father and son, one name, one address).

**What is left, and why:**
- 3 deleted people have records that never carried the email or phone in their request (a work
  email, blank fields). A request holding only those two hashes cannot reach them; the fix is upstream,
  in what the deletion intake collects.
- 8 moved: their old and new records share only the name. One key is not enough to delete, so name +
  zip single matches go to `deletion_review`: 7 persons, 1 of them a real leftover (14% precision).
  Last name + address alone was tried too and dropped: 354 persons, 3 real, so a reviewer would drown.
- 15 store orders of housemates sharing an inbox are still erased at load: orders carry no phone, so
  at load time there is nothing to tell them apart. Fixing it means holding ambiguous rows until
  identity has run instead of erasing at once. Open.
- 1 innocent vendor record: two Jonathan Deckers at one address, both born in 1945; the deleted one's
  tombstone came from a CRM row without a date of birth.

## Data products (dbt)

`dbt/` turns resolved records into the tables a marketing product actually reads, in the warehouse's
`products` schema. dbt runs inside the Dagster job and from `python -m plumbline`.

| Model | What it is |
| --- | --- |
| `dim_person` | The golden person: one row per person, each value with the source and date that vouched for it, conflict counts, staleness, orders, revenue, email engagement |
| `dim_person_address_history` | Where each person lived over time (`valid_from`, `valid_to`): "where did they live on 2025-06-30" is a range lookup |
| `fct_life_events` | Detected moves, each with the evidence behind it and a confidence level |
| `dim_household` | People at one current address |
| `mart_activation_audience` | Who may be contacted on which channel, and segments (customers, high value, lapsed, recent movers, work and personal identity linked) |

Scored against the answer key (seed 7; `python -m plumbline --score`):

| | Result | What it is compared with |
| --- | --- | --- |
| Current address | **90.9%** right | ceiling 91.8%: the other 8.2% moved and no source reported the new address |
| Address on a past date | 97.3% (2024-06-30), 95.3% (2025-03-31) | the true address on that date |
| Phone, date of birth, job title | 99.9%, 99.9%, 99.5% right when filled | |
| Work and personal email linked | **99.3%** right | of the 6,960 people for whom some source ever carried both |
| Moves used for segments | 91% precision, 70% of detectable moves found, a median 50 days after the move | 3,157 of 12,147 true moves were detectable at all |
| Households | precision 97.8%, recall 86.4% | true households |

What the scores changed, in order:

1. **Misspellings looked like moves.** "ROGERS AVE" and "ROGERSLAVENUE" were two addresses, which split
   the evidence and created false moves (precision 48%). Spellings are now merged per person (same house
   number, same zip or none, street lines at least 0.9 alike) before anything is chosen. Address accuracy
   went from 89.2% to 90.6%, move precision from 48% to 73%.
2. **Vendor dates are delivery dates.** A vendor re-sending an old address looked like a move back.
   Measured precision of a detected move by its evidence: store order 86%, CRM edit 79%, vendor only 45%,
   worse than a coin flip. Moves carry that confidence; segments use only store and CRM evidence (84% then, 91% after the namesake fix in identity).
   In production these rates would come from a labelled audit sample.
3. **"As of a date" is the end of that day.** Comparing timestamps to the bare date (midnight) dropped
   every event on the as-of day, and the compliance test caught 15 unsubscribes the audience would have
   emailed. One macro now defines it, with a regression test.
4. **Consent is not point in time.** An audience rebuilt for an earlier date included people who
   unsubscribed later. Attributes are as of a date; unsubscribes and deletions always apply.
5. **An unsubscribe belongs to the address.** Six came from family inboxes that identity resolution
   rightly attached to nobody; checking the address, not the person, covers them.

On address choice the scores were honest with me: "newest address that is still being confirmed" beats
"latest mention wins" by 0.4 points and "most trusted source wins" by 0.8. A small, consistent edge,
not a large one. Most of the remaining gap is the ceiling.

The gap between what is true and what a brand's own sources can see (only 26% of moves are detectable;
6,960 of 50,166 people ever show both a work and a personal email) is the size of the problem
third-party enrichment exists to close.

## Home-buyer model (Module C)

Who will buy a home in the next 180 days. Features come from `dbt/models/marts/fct_person_features.sql`,
built once per cutoff date with dbt's `as_of`, so each snapshot holds only what was known that day.
Property data (building size and class, from NYC PLUTO) joins to 97.3% of golden addresses on an exact
standardized key; Census median income joins by ZIP.

**The split cannot see the future.** Models are chosen on 2025-03-31 after training on 2024 cutoffs,
refit on all 2024 cutoffs, and reported once on 2025-06-30. A snapshot is used for training only if its
whole outcome window closed before the date it is evaluated on (`check_no_label_leak`). That guard
caught my own first schedule: a 182-day horizon let the last training snapshot see one day past the
test date, so the horizon is 180.

On 2025-06-30 (30,323 people, 660 bought within 180 days):

| | AUC | Top-decile lift |
| --- | --- | --- |
| Random | 0.48 | 0.8x |
| Census ZIP income alone | 0.63 | 2.0x |
| **Plumbline model** (logistic regression, chosen on validation over gradient boosting) | **0.661** | **2.3x** |
| Same model without property and Census enrichment | 0.611 | 1.8x |
| What-if: plus an on-time vendor feed (age, renter, job change, income band as of each cutoff), 5-seed gradient-boosting ensemble | 0.726 to 0.728 | 2.8x |
| Ceiling: the rule that generated the purchases | 0.806 | 4.4x |
| Same model with vendor data pasted back from December | 0.837 | 4.0x |

How to read it:

- **Gradient-boosting numbers are ranges.** One fit is not reproducible: it adds up floating-point
  numbers in parallel, and the order of those additions is not fixed. On identical data (same data
  fingerprint) one fit gave 0.7179 on a laptop and 0.7249 in the cloud, and two runs on the same
  laptop gave different single-fit ranges (0.7188 to 0.7236, then 0.7178 to 0.7295). Gradient
  boosting is therefore always a 5-seed ensemble, and the card records the spread. The ensemble has
  stayed within 0.726 to 0.728 on every run so far (0.7259 and 0.7270 on the laptop, 0.7284 in the
  cloud); it shrinks the wobble, it does not remove it. The model choice is safe from it: logistic regression wins on validation
  average precision by 0.0052 (0.0305 vs 0.0253), more than eight times gradient boosting's own seed
  spread (0.0006). Logistic regression is deterministic and gives the same AUC on every machine.

- **The leak beats the ceiling, which is how you know it is a leak.** Vendor attributes delivered in
  November and December say who owns a home after buying it. Pasted onto earlier snapshots they score
  above the best possible model, which only a model reading the future can do. `check_not_above_ceiling`
  refuses any backtest that does.
- **First-party data alone gets half the way.** The model captures 53% of the AUC above chance that the
  ceiling allows, and 38% of the lift. What drives buying here is household income, age, renting and a
  recent job change, which a brand's own systems barely see. An on-time vendor feed moves AUC from 0.66 to
  0.73: that difference is the price of enrichment, measured. The rest of the gap is exact household
  income, which nobody sells.
- **Calibration runs low.** Buying rose through 2025, so scores under-predict by about 1.5x in the top
  deciles. Use scores to rank; the model card says so.

Leaks found and fixed on the way, each now a test or a rule:

1. `records` (how many records a person has) was the top feature. It counted today's identity graph,
   and a buyer who moves gets a new store profile at the new address: the feature knew the outcome.
   It is now counted from what was known by the cutoff, and is no longer a feature.
2. Orders refunded after the cutoff were counted as refunded on the cutoff.
3. 365-day windows at a cutoff 90 days after the data starts are 90-day windows in disguise, so the
   same feature meant different things at different cutoffs. The drift check caught it at scoring time
   (index 0.49). Windows are now 90 days, which every snapshot has; a test enforces it.

**Scores are published only if the guardrails pass** (`src/plumbline/ml/guardrails.py`): the inputs
are the training inputs, scores are probabilities that vary, the mean is plausible, missing-value rates
are stable, and no feature drifts past a population stability index of 0.25. Every run is logged in
`ml.model_runs` with its checks; a failed run publishes nothing. People known only from the vendor feed
are not scored (33,432 on 2025-12-31): the model was trained before any vendor data existed, and they
are listed in `ml.home_buyer_not_scored` with that reason. Removing that rule makes the drift check
block publication, so the two protect each other. My first drift check had a bug of its own (it mixed
two pandas indexes and reported a drift of 18 on flags that had not moved); it has a regression test.

The model and its card are written to `data/models/home_buyer/<version>/`; the version is a hash of
the training data and configuration, and two runs from scratch produced the same one.
[The model card](docs/model_card_home_buyer.md) has the details.

## Income estimate and home-seller model

Two more models on the same point-in-time snapshots, with the same rules: chosen on validation,
reported on a later or held-out set, compared with a baseline and with the ceiling, and published only
if their gates pass (`src/plumbline/ml/income.py`, `src/plumbline/ml/seller.py`).

**Income band, for people the vendor never covered.** A brand gets an income band from the vendor
for some people and nothing for the rest. The model learns from the covered people and estimates the
uncovered ones from first-party behaviour, place and property data. The vendor's own columns (income,
age, homeowner) are never features: they are missing for exactly the people being estimated, so a
model using them would be trained on something it never sees when it runs. Households are kept whole
on one side of the split, so a person's partner cannot leak their income into the test.

| People without a vendor band, against their true band | Within one band | Mean band error | Rank correlation |
| --- | --- | --- | --- |
| Majority band for everyone | | | undefined |
| ZIP income alone | 79.2% | 0.91 | 0.46 |
| **Plumbline estimate** (5-seed gradient boosting) | **83.0%** | **0.84** | **0.55** |
| For comparison: the vendor's band, where it exists | 94.7% | | |

It is a modest gain, and the comparison row says why: what a brand sees about a person says little
about their income, and a bought band is much better. Estimates go to `ml.income_estimates` only if
they beat ZIP income alone, land within one band at least 60% of the time, and do not give everyone
the same band; otherwise nothing is published.

**Home seller: assessed, not published.** The question was who will sell their home in the next 180
days, among owners on the cutoff date. On validation the best candidate scored AUC 0.47, below chance;
on the test date 0.53, with a top-decile lift of 0.83. Even the rule that generated the moves reaches
only 0.56: in this world selling is close to random among owners, so there is almost nothing to learn.
All three gates failed (beats chance on validation, lift of at least 1.2, a ceiling that leaves
room), so no scores were written, and a stale `ml.home_seller_scores` table from an earlier run is
dropped rather than left looking current. Every assessment is logged in `ml.model_runs_seller`. I did
not change the world to make selling predictable: a model that should not ship is a result.

## Bronze on Iceberg, and deletion that reaches the files

`src/plumbline/lake/` publishes the bronze tables as Iceberg tables on any S3-compatible store, so
Spark, Trino or Snowflake can read them (`python -m plumbline.lake publish`, or `--lake` on the
pipeline). Publishing is a sync with the warehouse, which stays the source of truth: new rows are
appended as one snapshot, a rerun with nothing new commits nothing, and every earlier snapshot can
be read (time travel).

Time travel is also how a deleted person comes back. Deleting their rows from the current table
leaves them in last week's snapshot and in the Parquet files behind it. So when the warehouse has
purged anyone, the lake deletes the rows, expires every older snapshot, and removes every data file
and manifest nothing still needs. Deletion wins over history, on purpose.

That was not enough. Iceberg's default writes each column's lowest and highest value into the
manifests, cut to 16 characters, and a delete commit keeps an entry for the file it replaced, with
those values, in the live manifest. After deleting `aadams@kestrelmail.test`, the bucket still held
`aadams@kestrelma`. Tables are now created with `write.metadata.metrics.default = counts` (null and
value counts, no values). The test reads every file in the bucket, decoding Parquet values and
manifest bounds, and finds no trace of the address, not even a prefix. It runs against a local S3
server (moto), the same API as AWS S3 or MinIO; I did not run it against a real AWS account.

## Campaign experiment

The question a marketing data scientist gets most: did the campaign cause those sales, and who
should get the next one? `src/plumbline/experiments/` answers it for a winter reactivation email.
The world's response is planted in the answer key (`src/plumbline/synth/campaign.py`): lapsed
customers respond most, people who never ordered a little, and frequent buyers not at all (they
were buying anyway). A hidden per-person factor means no model can rank people perfectly.

1. **Design before the send** (`design.py`). Everyone the activation audience may email. The plan
   (metric, smallest effect worth detecting, power) is written to `exp.plan` before any result
   exists: 57,387 inboxes, a half-point effect detectable at 80% power.
2. **Randomize the inbox, not the person.** My first design randomized people, and the answer key
   showed the flaw: families share inboxes, so 516 inboxes landed in both arms and "held out"
   people read the email their sibling was sent. Assignment is now a salted hash of the address.
3. **Attribute orders through the identity graph.** People check out with their work address, or
   mistype one. An order counts for an inbox if it was placed with that address or any address the
   identity graph ties to its people. 89.1% of orders in the window were attributed; the rest count
   as "did not buy" in both arms, which shrinks the effect toward zero.
4. **Check before believing.** Sample ratio: 29,046 vs 28,341 inboxes, p = 0.003. That is above
   the alarm set in advance (0.001), and the same hash splits evenly under 40 other salts, so it is
   chance; I did not change the salt after seeing it. A test breaks the split on purpose and the
   readout marks itself untrustworthy.

| Bought within 30 days | Treatment | Holdout | Effect (95% CI) | True effect |
| --- | --- | --- | --- | --- |
| Everyone, as measured | 3.50% | 2.19% | +1.31 pts (+1.04 to +1.58) | +1.61 |
| **Everyone, corrected for unattributed orders** | | | **+1.47** (+1.16 to +1.77) | +1.61 |
| Lapsed customers | 6.16% | 2.09% | +4.07 (+3.39 to +4.74) | +4.45 |
| Never ordered | 1.11% | 0.57% | +0.54 (+0.35 to +0.74) | +0.63 |
| Recent customers | 7.57% | 7.23% | +0.33 (-0.65 to +1.31), Holm p = 0.50 | +1.23 |

Revenue per inbox rose $1.54 (CI $1.14 to $1.95). The raw interval misses the true effect: 11% of
orders cannot be tied to anyone, and they count as "did not buy" in both arms, which pulls the
measured effect toward zero. The correction divides by the attributed share and assumes the loss is
the same in both arms; with it, the truth is inside. That gap is the case for better attribution
(asking for the email at checkout), not for a cleverer formula. CUPED (adjusting for
each inbox's order history) narrowed the interval by only 0.9%: variance falls by the squared
correlation between history and outcome, and for a rare yes/no purchase that is close to zero. A
test pins that rule, on an outcome history does predict.

**Who to send it to next time.** An uplift model (two models, sent and held out; uplift is the
difference) learns who the email changes, using only what was known before the send. It is chosen
on one half of the inboxes and judged on the other: logistic regression beat gradient boosting on
validation Qini. Its ranking correlates 0.49 with the true effect (Spearman). Sending the next email to 30% of
the list:

| Policy | Extra buyers per 1,000 emails (from the answer key) |
| --- | --- |
| Send to everyone, or a random 30% | 16 |
| The 30% likeliest to buy | 24 |
| **The 30% the uplift model ranks highest** | **38** |
| Best possible 30% | 43 |

The likeliest buyers are the wrong target: many of them buy anyway, so the email adds little.

Also found: 2.4% of sends bounced, addresses in the golden record that belong to nobody (typos
the record kept). They are in the readout as bounces rather than hidden, and the true effect of
sending to the list counts them as zero, because the list is what was decided.

## Operations agent (Module E)

`src/plumbline/agents/` is the agent the team that runs Plumbline talks to, in Slack or from the
command line. It answers from the team's own documents, reads live state through read-only tools,
and changes nothing on its own: a write is a proposal until a second person with the right role
approves it. Every question, tool call, proposal and decision is in an audit log.

```text
message ─▶ route to a skill ─┬─ question about how or why ─▶ docs (BM25) ─▶ cited answer, or "not in our documents"
                             ├─ read tool ─▶ warehouse opened read-only ─▶ answer, with the runbook to follow
                             └─ write tool ─▶ proposal ─▶ second person with the role approves ─▶ runs once ─▶ audit
```

| Skill | Owner | Tools | What it does |
| --- | --- | --- | --- |
| `knowledge_qa` | data-platform | none | Answers from README, runbooks, model and data cards; cites every claim; refuses when they are silent |
| `pipeline_status` | data-platform | `ingest_status`, `data_products_status`, `model_status` | "Did last night's load reject anything?", "Is the model published?" |
| `privacy_review` | privacy | `deletion_review_queue`, `resolve_deletion_review` | Lists open deletion-review cases; proposes closing one |
| `docs_freshness` | docs | `check_doc_claims`, `update_doc` | Checks documented facts against the warehouse; proposes the exact edit |
| `access_admin` | operations | `access_record`, `grant_role`, `revoke_role`, `offboard_person` | Who holds which role; proposes grants, removals and offboarding for an admin to approve |
| `security_questionnaire` | compliance | none | Drafts a questionnaire from `docs/security.md` and the runbooks, one source per answer |

Each skill is one YAML file in `skills/` with an owner, the tools it may use, its eval, and how many
minutes the job takes by hand (with a note saying what that manual job is). A skill
that names an unknown tool, has no owner, or no eval file is refused at load.

**Measured** (`python -m plumbline.agents eval`):

| | Result |
| --- | --- |
| Right section in the top 3 results | 44 of 49 answerable questions |
| Answered right, with the right source | 18 of 25 (dev half), **19 of 24 (test half)**; the refusal threshold (0.45) was chosen on the dev half only |
| Refused when the documents have no answer | 9 of 9 (dev), **7 of 9 (test)**: a 401(k) "match" and an "AWS account" each share enough words with a section to get through |
| Routed to the right skill and tool | 32 of 32 |
| Security questionnaire, drafted right with the right source | 20 of 20 (dev half), **17 of 20 (test half)** |
| Security questionnaire, left for a person when the controls page does not settle it | 7 of 7 (dev), **6 of 6 (test)** (certifications, audits, insurance, retention, incident timelines, a data protection officer) |

The eval sets were tripled (from 32 to 67 knowledge questions, 18 to 53 questionnaire items),
written before any of the new ones was run, and each expected answer was checked to exist in its
source section. Doing that found two grader bugs: YAML read `off` as false and `[33,287]` as two
numbers, and a right answer whose phrase wrapped across a line was graded wrong. With more dev
questions the knowledge threshold moved from 0.5 to 0.45: more right answers, and one more
unanswerable question answered. Each new section of documentation makes lexical refusal a little
worse (the lake section let the AWS question through); judging whether the passages actually answer
the question is the job model mode is for, and it is measured separately with your key.

These numbers are for the offline mode, which quotes the best-matching bullets and table rows with
their source. What it misses: different words for the same thing ("runs the whole pipeline" vs "every
stage in order"), answers split across two sections, and a plausible but wrong section ranked first.
A language model reading the retrieved passages is what closes those; it is optional
(`PLUMBLINE_LLM_MODEL`, `PLUMBLINE_LLM_API_KEY`, any OpenAI-compatible endpoint) and its answers are
checked before anyone sees them: every sentence must cite a passage it was given, or the quoted
answer is shown instead. Its accuracy is not reported here because it was not measured without a key.

**What the evals changed.** Coverage first ignored words the documents never use, so an HR question
looked fully covered by any passage that shared one ordinary word with it; unseen words now count
against it. (The documents never quote the eval's questions: a first draft of this section did, the
agent then found them here, and refusals fell from 8 of 8 to 6 of 8.) Quoting single sentences missed answers that share no words with the question (a runbook
bullet states the symptom, then the remedy): the unit is now the whole bullet, and a table row is
quoted with its header. Answer accuracy went from 8 to 17 of 24, with the held-out half rising too.
Adding this section to the README (which the agent also searches) then cost one answer and one
retrieval: more text competes for the top spot. The table above is measured on the final documents.

**Guardrails, each held by a test, each checked by breaking it on purpose (6 of 6 caught):**

- a write runs only after approval by someone with the tool's role who is not the person who asked;
  approving twice does nothing the second time
- read tools open the warehouse read-only; a bug in one cannot change data
- tool output carries no names, emails or phones, and the audit log keeps arguments as hashes
- Slack requests must carry a valid signature less than five minutes old (forged and replayed
  requests get 401); a retried event or a double click runs once; bot messages are ignored
- a document edit refuses if the passage changed since it was proposed, and only Markdown files
  inside the repository can be edited
- model and Slack credentials come from the environment and never appear in a log or repr

**Access provisioning.** Roles live in `ops.access`, not in a file: `ops/roles.yaml` only seeds the
first holders of an empty record. Granting a role, removing one, or offboarding a person (every role
removed, their open requests closed) is a proposal that an admin approves. The admin may not be the
person who asked, and nobody may approve a change to their own access. Removing the last admin is
refused, because nobody could approve anything again. The access record writes itself: each grant
and removal is a row with who approved it, when, and through which request, so an access review is a
query, not a spreadsheet someone keeps.

**Security questionnaires.** `python -m plumbline.agents questionnaire in.csv out.csv` reads a
`question` column and writes a draft answer, its source section and a status per question.
`docs/security.md` states the controls as they are, including the ones Plumbline lacks (no
encryption at rest, no user accounts, no automated backups), because a questionnaire is signed as
fact. A question the documents do not settle is marked "needs a person", not guessed. Questionnaire
items are long and use words the controls page does not, so they have their own refusal threshold,
chosen on the dev half by the same rule as the knowledge eval (0.25). A test re-runs that choice
on the current documents and fails if the constant no longer matches: an earlier draft of the runbook
moved the tie, which is how I found out the choice depends on the documents. With 9 dev questions
several thresholds tied; with 27, 0.25 is the single best. Suffix stemming ("encrypted",
"encryption") helped a little.

**Hours retired.** `python -m plumbline.agents usage` now multiplies each skill's successful uses
(an answer that was not a refusal, a read that worked, an approved change that ran) by the minutes
its owner says the job takes by hand. The count is measured; the minutes are an owner's estimate and
should be re-timed. There are no real users yet, so there is no real number to report.

**It found a real problem.** The docs freshness check found that runbook section 3 still said deletion
was exact-match only, written before person-level deletion existed. The agent proposed the edit, the
person who asked could not approve it, a second person with the docs role did, and it ran once. The
nightly Dagster run now carries `docs_match_the_warehouse`, which warns when a documented fact drifts.

Building it also found a pipeline bug: each nightly run appended the same deletion-review cases
again (7, 14, 21 rows for 7 persons). Cases are now queued once; a test plants a case and reruns.

Adoption and cost: `python -m plumbline.agents usage` reports requests, refusals, people, latency,
model tokens and cost per skill, decision times for proposals, hours retired, and skills nobody used.
Setting up Slack, secrets and roles: [the agent runbook](docs/runbooks/agent.md).

## Scale

The same command at three sizes (`python scripts/scale_run.py --n-people N`), every stage from
generation to the experiment readout, on a 2-core, 7 GB cloud machine. Seconds per stage, and the
most memory the process had used by the end of the run:

| People | Rows delivered | Store sync | Ingest | Identity | dbt products | Models | Experiment | Whole pipeline | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20,000 | 62,729 | 6 | 6 | 14 | 20 | 90 | 4 | 2.4 min | 2.4 GB |
| 100,000 | 314,579 | 2 (incremental) | 32 | 36 | 39 | 183 | 14 | 5.3 min | 2.6 GB |
| 300,000 | 946,299 | 100 | 86 | 94 | 84 | 400 | 31 | 14.6 min | 3.1 GB |

And whether the answers hold as the world grows:

| People | Identity precision / recall | Deleted people with data left | Current address right (ceiling) | Home-buyer AUC (ceiling) | Experiment: true effect inside the corrected interval |
| --- | --- | --- | --- | --- | --- |
| 20,000 | 99.6% / 98.9% | 0 of 100 | 91.3% (92.0%) | 0.675 (0.810) | yes |
| 100,000 | 99.4% / 98.8% | 11 of 500 | 90.9% (91.8%) | 0.661 (0.806) | yes |
| 300,000 | 99.3% / 98.8% | 15 of 1,500 | 91.2% (92.0%) | 0.664 (0.797) | yes |

What the first scale run found, and what changed:

1. **Identity got worse as the world grew** (precision 99.1%, 97.4%, 93.5%): namesakes multiply
   faster than people, and a too-generous email-typo rule joined them. Fixed in the matcher, above;
   precision is now flat.
2. **The store sync was quadratic**, but in my test server, not the connector: the mock API re-sorted
   every order for every page, 537 s at 300,000 people. It now keeps a sorted index, like the
   database a real store pages on, and a test proves it returns exactly what the slow version did.
   Most of what remains is honoring `Retry-After` on 177 throttled requests, which a real store
   would impose too.
3. **Memory is DuckDB's buffer cache more than the data.** Identity jumps to 2.4 GB already at
   20,000 people because DuckDB takes memory it is allowed to; it grows 30% from 20,000 to 300,000.

4. **1,000,000 people found two wrong assumptions, not a slow stage.** On a 10-core laptop the run
   first crashed in scoring, on two people the store data cannot tell apart (item 6 under identity).
   Then a dbt test failed in a feature snapshot: `every_address_spelling_has_one_canonical`. Address
   spellings are merged when they look alike, but alike is not transitive ("1901 DORCHESTERROAD APT
   26" is like "1901 DORCHESTER ROAD APT 26", which is like "1901 DORCHESTER AVE", which the first is
   not like), so a spelling's canonical could itself point elsewhere. Pointers are now followed to
   their end; at 100,000 people the canonical table and every score are unchanged, because no chain
   exists there. The test that caught it was written in Phase 2 for exactly this promise. On the 2-core, 7 GB machine the same run is killed for memory
   during the identity stage, so that machine's ceiling is between 300,000 and 1,000,000 people.
   Generating the 1,000,000-person world and its store took 3.4 GB and 6 minutes.

The full 1,000,000 numbers come from `python scripts/scale_run.py --n-people 1000000` on a machine
with more memory and land in `data/scale/n1000000.json`. Nothing here claims terabytes: everything
runs on one machine.

## How ingestion works

Each source is one YAML contract in `sources/`: its files, key, column types and which columns
identify a person. Adding a source is a new YAML file and nothing else
(`test_new_source_onboards_with_yaml_only`).

| Promise | How |
| --- | --- |
| A file loads at most once | its id is the SHA-256 of its bytes, recorded in the same transaction as its rows |
| A row is stored at most once | rows are normalized, then hashed; a known hash is skipped |
| Bad rows never load silently | quarantine with a reason code; a file missing a required column is rejected whole |
| A corrected `_v2` replaces `_v1` | `delivery_rows` remembers which files contained each row; only the newest version counts |
| Newer facts win, replays do not | latest by the source's own date columns, then by when the content first arrived |
| Deleted people stay deleted | purged from storage and quarantine; blocked before validation in every later load |

The online store is an API, not a file drop. `src/plumbline/ingest/connectors/store_api.py` pulls
it incrementally (watermark minus a 5-minute overlap, keyset cursors), retries 429s after the
server's Retry-After and 5xx or dropped connections with jittered backoff, stops at once on a 401,
and lands each page as a CSV atomically before saving its cursor. Landing as files means every
promise above applies to API data with no extra code. The store itself is a local mock
(`src/plumbline/store/server.py`) that throttles, fails, cuts responses in half and refunds orders
while you page through it.

Details and the reasons behind each choice are in `src/plumbline/ingest/engine.py`.
Recovery steps are in [the runbook](docs/runbooks/ingestion.md).

## Run it

```bash
python -m pip install -e ".[dev,orchestration,lake]"
python -m pytest -q                                      # use python -m, so the venv's pytest runs
export CENSUS_API_KEY=...                                # free: api.census.gov/data/key_signup.html
python scripts/fetch_reference.py --out data/reference  # once; needs internet; safe to rerun
python -m plumbline.synth.cli --ref data/reference --out data/synth --n-people 100000 --seed 7
python -m plumbline --score                              # every stage in order, with accuracy receipts
python -m plumbline.agents ask "did last night's load reject anything?"
python -m plumbline.agents eval                          # knowledge, routing (and the model, if configured)
dagster dev -m plumbline.ingest.orchestration            # optional: the same pipeline, with checks, in a UI
```

`python -m plumbline` builds the store, syncs it through the faulty mock API, ingests every source,
resolves persons, applies deletions, builds the dbt data products with their tests, trains the
home-buyer model and publishes its scores if the guardrails pass, estimates income, assesses the
home-seller model, reads out the campaign experiment, then prints one summary with time and peak
memory per stage. Add `--fresh` to start from an empty warehouse, `--lake` to also publish bronze to
the Iceberg lake. Each stage also has its own command for working on one at a time:

```bash
python -m plumbline.store build | sync
python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb
python -m plumbline.identity --score data/synth/seed7_n100000
python -m plumbline.privacy --score data/synth/seed7_n100000
python -m plumbline.ml          # snapshots, training, model card, guarded scoring
```

**In Docker** (the image holds code only; data is the mounted `./data`, secrets come from `.env`):

```bash
docker compose run --rm pipeline              # after generating data/synth as above
docker compose up agent dagster               # Slack endpoint on :3000, Dagster on :3001 (localhost only)
docker compose --profile lake up -d minio && docker compose --profile lake run --rm lake   # S3 lake on MinIO
```

CI builds the image on every push, checks that no `.env`, roles file or data is inside it and that it
runs as a non-root user, and runs the test suites inside it.

## The synthetic world

To measure how well a pipeline matches the same person across sources, you need to know who is
really who. Real consumer data never tells you, and building on real people's contact details
would be a privacy harm. So the people are invented and the answer key is kept separately, the
way you bury coins in a field before testing a metal detector. The places and incomes are real:
people live on real NYC tax lots (PLUTO, owner names never loaded) with household incomes drawn
from Census median incomes for their ZIP. Every planted rule is listed in
[the data card](docs/synthetic_data_card.md).

| Path in a run | Contents | Who may read it |
| --- | --- | --- |
| `sources/crm/` | full extract plus two daily change files | the pipeline |
| `sources/vendor/` | 8 weekly files; column rename from week 5; week 6 re-sent with fixes; repeated rows | the pipeline |
| `sources/email/` | 14 daily files; some arrive late; one day corrected twice | the pipeline |
| `sources/privacy/` | deletion requests as SHA-256 hashes | the pipeline |
| `deliveries.json` | every file with logical date, arrival time, version, hash | the pipeline |
| `truth/` | people, households, histories, life events, record links, damage log | evaluation only |
| `manifest.json` | config, library versions, counts, content fingerprint | everyone |

Generator guarantees (tested): same seed gives the same fingerprint on different machines
(`5ea669fd456d7b9e` for seed 7 on both a Mac and a Linux workspace); only reserved `.test` and
`.example` email domains and the fictional 555 area code; a reference file with any `owner*`
column is refused; corrections only move values toward the truth.

Seed 7 sanity checks against the truth tables: median household income $81,200, 24.5% owners,
6.6% of households buy a home over two years. These describe the synthetic world, not New York.
