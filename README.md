# Plumbline

**A consumer data platform where every value has a receipt.**

100,000 invented New Yorkers, with a hidden answer key, reach a fictional outdoor brand through
four messy feeds: a CRM, a third-party data vendor, email events and an online store API that
throttles, crashes and changes data mid-sync. Plumbline loads them, works out which records are the
same person, and deletes people who ask to be deleted, everywhere. Every number below is measured
against the answer key, and the tests that hold each one are named.

**At a glance** (seed 7, 100,000 people, 314,579 rows, one command, 72 s on a laptop-class machine):

| | Result |
| --- | --- |
| Ingestion | 0 invariant violations; 100 injected faults change nothing; a crashed API sync resumes without loss |
| Identity resolution | F1 **0.981** (precision 97.4%, recall 98.8%) vs 0.927 for exact email-or-phone matching |
| Person-level deletion | deleted people with data left: 23 → **11** of 500; records of people who never asked, removed: 69 → **50** |
| Golden person (dbt) | current address right for 90.6% of people, against a ceiling of 91.8% (the rest moved and no source saw it); work and personal identities linked 98.9% right |
| Home-buyer model | AUC 0.659 and 2.3x lift in the top decile on a later, untouched period; the best possible is 0.806; a leaky version scoring 0.838 is caught because it beats the best possible |
| Tests | 95 Python tests and 27 dbt tests, each a stated promise; deliberate breaks show which ones bite |

| Phase | What | Status |
| --- | --- | --- |
| 0 | Synthetic world and answer key | Done |
| 1 | Module A: ingestion, including the store API connector | Done |
| 2 | Module B: identity resolution, person-level deletion, data products (dbt) | Done |
| 3 | Module C: home-buyer model with point-in-time features and publishing guardrails | Done |
| 4 | Modules D and E: agents | Planned |

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
| Plumbline: Splink + cannot-link clustering | **97.4%** | **98.8%** | **0.981** | **97.9%** |

Recall by damage type (share of a record's true partners it was joined with): nickname 91.5%,
name typo 96.1%, missing zip 96.6%, missing phone 97.3%, missing email 97.5%, address typo 97.8%,
email typo 98.2%, undamaged 99.3%. The baseline gets 68 to 72% on missing or misspelled contact fields.
F1 stays between 0.975 and 0.987 for any match threshold from 0.5 to 0.99; 0.9 was fixed before scoring.
Email events, which carry only an address, attach to a person 92.3% of the time and are right 99.1%
of the time; an address shared by a family is left unattached on purpose.

What it took, in the order the problems showed up:

1. **Households look like matches.** The first model had 32% precision: training on blocks of
   "same last name and zip" taught EM that a family is a match. Training on blocks where families
   cannot dominate (same phone; same first and last name) fixed the weights.
2. **Usernames repeat the name.** `brandonanderson@` at two providers scored +13 bits, counting the
   name twice. The email comparison now has only exact and within-2-edits levels.
3. **Chains.** Connected components joined two spouses through a record with no first name.
   Clustering now merges strongest-first and refuses any merge that puts two clearly different first
   names, or two birth years, in one person (cannot-link). Mixed-up clusters fell from 12,065 to 515.
4. **Non-determinism.** With refusals, merge order matters; DuckDB returns ties in a different order
   each run, and cluster names depended on row order. Both are now total orders, with a regression test.

The matcher never sees how the data was damaged: its nickname and street-suffix tables are written
independently of the generator (a test fails if it imports the generator or reads the answer key).
Its nickname list covers 38 of the generator's 40; Tina for Christina was not added after the scores
showed it missing, because that would be tuning to the answer key.

Remaining error class: namesakes (two Patrick Lewises whose name-built emails differ by one character).

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
| Current address | **90.6%** right | ceiling 91.8%: the other 8.2% moved and no source reported the new address |
| Address on a past date | 97.0% (2024-06-30), 95.0% (2025-03-31) | the true address on that date |
| Phone, date of birth, job title | 99.6%, 99.5%, 99.2% right when filled | |
| Work and personal email linked | **98.9%** right | of the 6,931 people for whom some source ever carried both |
| Moves used for segments | 84% precision, 70% of detectable moves found, a median 50 days after the move | 3,143 of 12,079 true moves were detectable at all |
| Households | precision 97.1%, recall 85.8% | true households |

What the scores changed, in order:

1. **Misspellings looked like moves.** "ROGERS AVE" and "ROGERSLAVENUE" were two addresses, which split
   the evidence and created false moves (precision 48%). Spellings are now merged per person (same house
   number, same zip or none, street lines at least 0.9 alike) before anything is chosen. Address accuracy
   went from 89.2% to 90.6%, move precision from 48% to 73%.
2. **Vendor dates are delivery dates.** A vendor re-sending an old address looked like a move back.
   Measured precision of a detected move by its evidence: store order 86%, CRM edit 79%, vendor only 45%,
   worse than a coin flip. Moves carry that confidence; segments use only store and CRM evidence (84%).
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
6,931 of 49,918 people ever show both a work and a personal email) is the size of the problem
third-party enrichment exists to close.

## Home-buyer model (Module C)

Who will buy a home in the next 180 days. Features come from `dbt/models/marts/fct_person_features.sql`,
built once per cutoff date with dbt's `as_of`, so each snapshot holds only what was known that day.
Property data (building size and class, from NYC PLUTO) joins to 96.8% of golden addresses on an exact
standardized key; Census median income joins by ZIP.

**The split cannot see the future.** Models are chosen on 2025-03-31 after training on 2024 cutoffs,
refit on all 2024 cutoffs, and reported once on 2025-06-30. A snapshot is used for training only if its
whole outcome window closed before the date it is evaluated on (`check_no_label_leak`). That guard
caught my own first schedule: a 182-day horizon let the last training snapshot see one day past the
test date, so the horizon is 180.

On 2025-06-30 (30,145 people, 655 bought within 180 days):

| | AUC | Top-decile lift |
| --- | --- | --- |
| Random | 0.49 | 0.9x |
| Census ZIP income alone | 0.63 | 2.0x |
| **Plumbline model** (logistic regression, chosen on validation over gradient boosting) | **0.659** | **2.3x** |
| Same model without property and Census enrichment | 0.610 | 1.8x |
| What-if: plus an on-time vendor feed (age, renter, job change, income band as of each cutoff), 5-seed gradient-boosting ensemble | 0.728 | 2.8x |
| Ceiling: the rule that generated the purchases | 0.806 | 4.4x |
| Same model with vendor data pasted back from December | 0.838 | 4.0x |

How to read it:

- **Gradient-boosting numbers are ranges.** One fit is not reproducible across machines: it sums
  histograms over CPU threads, so rounding differs with the thread count. On identical data (same
  data fingerprint on both machines) one fit gave 0.7179 on a laptop and 0.7249 in the cloud; five
  seeds range from 0.717 to 0.728. Gradient boosting is therefore always a 5-seed ensemble, and the
  card records the spread. The model choice is safe from it: logistic regression wins on validation
  average precision by 0.0043, more than three times gradient boosting's own seed spread (0.0013).
  Logistic regression is deterministic and gives the same 0.6588 on every machine.

- **The leak beats the ceiling, which is how you know it is a leak.** Vendor attributes delivered in
  November and December say who owns a home after buying it. Pasted onto earlier snapshots they score
  above the best possible model, which only a model reading the future can do. `check_not_above_ceiling`
  refuses any backtest that does.
- **First-party data alone gets half the way.** The model captures 52% of the AUC above chance that the
  ceiling allows, and 39% of the lift. What drives buying here is household income, age, renting and a
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
are not scored (33,287 on 2025-12-31): the model was trained before any vendor data existed, and they
are listed in `ml.home_buyer_not_scored` with that reason. Removing that rule makes the drift check
block publication, so the two protect each other. My first drift check had a bug of its own (it mixed
two pandas indexes and reported a drift of 18 on flags that had not moved); it has a regression test.

The model and its card are written to `data/models/home_buyer/<version>/`; the version is a hash of
the training data and configuration, and two runs from scratch produced the same one.
[The model card](docs/model_card_home_buyer.md) has the details.

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
python -m pip install -e ".[dev,orchestration]"
python -m pytest -q                                      # use python -m, so the venv's pytest runs
export CENSUS_API_KEY=...                                # free: api.census.gov/data/key_signup.html
python scripts/fetch_reference.py --out data/reference  # once; needs internet; safe to rerun
python -m plumbline.synth.cli --ref data/reference --out data/synth --n-people 100000 --seed 7
python -m plumbline --score                              # every stage in order, with accuracy receipts
dagster dev -m plumbline.ingest.orchestration            # optional: the same pipeline, with checks, in a UI
```

`python -m plumbline` builds the store, syncs it through the faulty mock API, ingests every source,
resolves persons, applies deletions, builds the dbt data products with their tests, trains the
home-buyer model and publishes its scores if the guardrails pass, then prints one summary. Add `--fresh` to start from an
empty warehouse. Each stage also has its own command for working on one at a time:

```bash
python -m plumbline.store build | sync
python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb
python -m plumbline.identity --score data/synth/seed7_n100000
python -m plumbline.privacy --score data/synth/seed7_n100000
python -m plumbline.ml          # snapshots, training, model card, guarded scoring
```

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
