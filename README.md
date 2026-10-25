# Plumbline

**A consumer data platform where every value has a receipt.**

Invented people with a hidden answer key arrive through the kind of messy feeds real vendors
send. Plumbline loads those feeds, will match people across them and model them, and every
number it reports is measured against the answer key.

| Phase | What | Status |
| --- | --- | --- |
| 0 | Synthetic world and answer key | Done |
| 1 | Module A: ingestion (this release) | Core done; mock store API, Iceberg and Trino next |
| 2 | Module B: identity matching and data products | Planned |
| 3 | Module C: models | Planned |
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
python -m plumbline.store build                          # store orders from the synthetic customers
python -m plumbline.store sync                           # pull them through the faulty mock API (~1 min)
python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb
python -m plumbline.identity --score data/synth/seed7_n100000     # persons; --score prints accuracy
python -m plumbline.privacy --score data/synth/seed7_n100000      # person-level deletion and its receipts
dagster dev -m plumbline.ingest.orchestration            # optional: the same load, with checks, in a UI
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
