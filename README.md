# Plumbline

[![tests](https://github.com/AKilalours/plumbline/actions/workflows/tests.yml/badge.svg)](https://github.com/AKilalours/plumbline/actions/workflows/tests.yml)

**A consumer data platform where every value has a receipt.**

100,000 invented New Yorkers, with a hidden answer key, reach a fictional outdoor brand through
four messy feeds: a CRM, a third-party data vendor, email events and an online store API that
throttles, crashes and changes data mid-sync. Plumbline loads them, works out which records are the
same person, and deletes people who ask to be deleted, everywhere. Every number below is measured
against the answer key, and the tests that hold each one are named.

**At a glance** (seed 7, 100,000 people, 314,839 rows, one command; time and memory per stage at 20,000 to 300,000 people are in [Scale](#scale)):

| | Result |
| --- | --- |
| Ingestion | 0 invariant violations; 100 injected faults change nothing; a crashed API sync resumes without loss |
| Identity resolution | F1 **0.991** (precision 99.4%, recall 98.8%) vs 0.927 for exact email-or-phone matching, and it holds as the world grows (0.991 at 300,000 people), where the first version fell to 0.960 |
| Person-level deletion | deleted people with data left after load: 18 → **8** of 500 after person-level deletion, with **0** people deleted who never asked; the first version deleted 8 innocent people at 300,000 |
| Golden person (dbt) | current address right for 91.1% of people, against a ceiling of 91.9% (the rest moved and no source saw it); households 99.98% precise; work and personal identities linked 99.2% right |
| Home-buyer model | AUC 0.660 and 2.0x lift in the top decile on a later, untouched period; the best possible is 0.797; a leaky version scoring 0.836 is caught because it beats the best possible |
| Income estimate | for people the vendor never covered: within one band of the true income 83.6% of the time, against 79.8% for ZIP income alone; the vendor's own band, where it exists, gets 94.9% |
| Campaign experiment | a holdout test of a reactivation email, checked against the answer key's true effect: inside the attribution-corrected 95% interval in 7 of 8 runs (20,000 to 1,000,000 people); the run that missed is the one whose readout marked itself untrustworthy; from 100,000 people up, an uplift model sends the next email to the 30% it changes most, for 32 to 39 extra buyers per 1,000 emails against 24 for "the likeliest buyers" (at 20,000 it has too little to learn from and does not win) |
| Home-seller model | **not published**: validation AUC 0.54, and even the rule that generated the data reaches only 0.55; the guardrails refused it, as they should |
| Operations agent | answers from the team's docs with a citation on every claim (on 67 eval questions: 19 of 24 right on the held-out half, the right section in the top 3 for 45 of 49, and 7 of 9 unanswerable ones refused); reads live state read-only; changes nothing until a second person with the right role approves; found and, after approval, fixed a stale runbook passage |
| AI features, measured | with gpt-4.1-mini, on held-out halves: the agent picks the right tool for 19 of 20 hard messages (keyword rules: 12 of 20); questions in English answered right 14 of 14 with no contact data ever returned; doc answers refuse what the docs do not say 9 of 9 (quoted: 7 of 9). Where it did not win (doc answers tie, contract types one worse) is in [AI, measured](#ai-measured). Code checks every model output before anything runs, and recorded runs replay without a key |
| Model release gate | on 100,000 people it held all three challengers, including one with the champion's AUC whose probabilities were off by 0.44 on average: an AUC-only gate would have shipped it |
| Access and questionnaires | roles granted, removed and offboarded only by an admin who neither asked nor is the person; every change recorded with who approved it and through which request; security questionnaires drafted from the controls page with a source per answer, and controls we do not have left for a person |
| Tests | 307 Python tests and 27 dbt tests, each a stated promise; deliberate breaks show which ones bite |

## See it work

`python -m pip install -e ".[app]"` then `python -m streamlit run streamlit_app.py`. The app builds a city of 4,000
people with the real pipeline (generator, store API sync, ingestion, identity, deletion, dbt) in
about a minute. Every button does real work on a private copy, and the city never changes. Seven
places, almost no text; every number has its evidence one click away:

- **Overview**: six live checks (clean, current, conflicts, identity, consent, deletion) and open incidents.
- **Sources**: delivery health, a failure lab that throttles and crashes the store API, and AI-drafted
  contracts for partner files nobody described.
- **Identity**: every person's records against the answer key, with split, merge and undo; households;
  person-level deletion with a replay that proves nobody comes back.
- **Audiences**: typed rules from a sentence, English to guarded SQL, and the email experiment.
- **Models**: the release gate, the AI scorecard, and the scale runs.
- **Agent**: the incident lab and the operations agent. Inject one of four real incidents (a false
  identity merge, an interrupted store sync, a warehouse restored from a backup older than a deletion
  request, a training snapshot filled with values from the future). Checks a team would run detect it
  without the answer key; a timed trace shows each step; the fix is proposed by the agent and runs only
  after a person with the right role approves (the agent cannot approve itself, and an approval runs
  once); verification re-runs the checks and asks the answer key; the incident report is generated
  from the run record.
- **Activity**: every action, by whom, and every incident report.

The styling borrows Minerva's palette and type as a nod to the job it was built for. Plumbline is an
independent project, not Minerva's platform, and uses none of its data, name or logo in the app.

Use `python -m streamlit`, not a bare `streamlit`: the first run of the app found Anaconda's
Streamlit on the PATH, a Python without the project's libraries, and every page failed on a missing
module. The app now checks its own Python first and, if anything is missing, says which and how to
start it right (`test_a_python_without_the_projects_libraries_gets_one_clear_message`).

The live city is small so the app fits a free host, which allows about 2.7 GB: identity's DuckDB is
capped at 1 GB there (`PLUMBLINE_DUCKDB_MEMORY`). No language model is called from the public page
unless it is started with `PLUMBLINE_APP_USE_LLM=1`, because a public page holding an API key is a
page anyone can spend it from.

| Phase | What | Status |
| --- | --- | --- |
| 0 | Synthetic world and answer key | Done |
| 1 | Module A: ingestion, including the store API connector | Done |
| 2 | Module B: identity resolution, person-level deletion, data products (dbt) | Done |
| 3 | Module C: home-buyer model with point-in-time features and publishing guardrails | Done |
| 4 | Module E: operations agent (knowledge with sources, read-only tools, approvals, audit, Slack) | Done |
| 5 | Income estimate, home-seller assessment, access provisioning, security questionnaires, hours retired | Done |
| 6 | CI, campaign experiment and uplift model, Iceberg lake, scale runs, bigger evals, Docker | Done |
| 7 | Demo app (control room, failure lab, identity edits, replay, audiences, activity log); the last wrongly deleted record at 1,000,000 people traced and fixed; identity memory capped | Done |
| 8 | AI features with held-out evals and recorded runs (tool planner, contract drafter, English to SQL); release gate; learned identity re-scorer measured and not shipped | Done; model run recorded (gpt-4.1-mini) |
| 9 | Seven-place app with minimal text; incident lab (inject, detect, inspect, approve, fix, verify, report) for four incidents | Done |

## AI, measured

Each model feature has a way that works without a model, a held-out half the model never tuned on,
and code that checks the model's output before anything happens. `python -m plumbline.ai.evals`
runs all of them with the model named in `.env` (`PLUMBLINE_LLM_MODEL`, `PLUMBLINE_LLM_API_KEY`;
any OpenAI-compatible endpoint), records every call by prompt hash in `docs/results/ai/`, and writes
`summary.json`. The tests and the public app replay those recordings, so they never hold a key, and
a replayed answer is always one the model really gave for exactly that prompt.

| Feature | What code guarantees, whatever the model says | Without a model | With gpt-4.1-mini |
| --- | --- | --- | --- |
| The agent picks the tool (`ai/planner.py`, 40 messages in `evals/routing_hard.yaml`: paraphrases, typos, injected "approvals are off") | a typed tool call; ids must appear in the message; roles must exist; writes are proposals a second person approves | keyword rules: 12 of 20 | **19 of 20** |
| Answers from the docs (`agents/answer.py`, 67 questions) | every sentence cites a passage that was given, or the answer is replaced by quoted text | quoted: 19 of 24 right, 7 of 9 refusals | 19 of 24 right, **9 of 9 refusals** |
| A contract for a partner file nobody described (`ai/onboard.py`, 8 files in `evals/onboarding.yaml`) | the draft passes the same loader as every contract and is written to `sources/drafts/`, never applied | rules: 7 of 7 email and phone columns, 31 of 34 types, 3 of 4 keys | 7 of 7 email and phone, 30 of 34 types, 4 of 4 keys, 34 of 34 identity roles (rules: 31 of 34) |
| Questions in English become SQL (`ai/ask_data.py`, 34 questions with gold queries) | one SELECT, data products only, an allow-list of columns with no contact data, no file or URL readers, a row cap, read-only with a time limit | none: without a model there is no SQL | **14 of 14 answered right, 3 of 3 refused**; contact data returned: 0 |

Measured on 2026-10-29 with gpt-4.1-mini, recorded in `docs/results/ai/` and replayed by
`test_the_recorded_run_replays_exactly_and_the_readme_reports_it`. Read it as:

- **Tool choice is the clear win.** 19 of 20 against 12 of 20 for the keyword rules,
  which miss every paraphrase. The one held-out miss is still safe: asked "who are the admins right now?",
  the model passed the word "admin" as a user id, and the code refused it before anything ran. On the
  dev half it chose "check the docs" where "fix the docs" was asked (the safer of the two) and read
  an AUC question as a status check.
- **Answers tie; refusals improve.** 19 of 24 right either way. The model refused all
  9 questions the docs cannot answer, where quoting let a 401(k) "match" and an "AWS
  account" through. The citation check rejected 8 of its answers (a sentence with no
  citation) and showed quoted text instead. One held-out miss is a right answer in other words ("keep
  the broken warehouse file") that the phrase grader does not accept; the grader was fixed before the
  run and is not changed after it. The retrieval bar for model mode (0.35) was chosen on the dev half.
- **Contracts are mixed, and the rules stay.** Both caught every email and phone column with no false
  alarms. The model got every identity role and every key; the rules got one more column type right.
  Drafts stay drafts either way: a person approves them.
- **English to SQL** answered every held-out question right and declined every request for contact
  data, a delete or an export. The one dev miss counted "moved recently" from the life-events table
  instead of the `recent_mover` flag: a definition question, which is why the gold queries exist.

To run it again, put two lines in `.env` (git-ignored; the command reads it and prints names, never
values), `PLUMBLINE_LLM_MODEL=<a chat model your key can use>` and `PLUMBLINE_LLM_API_KEY=<your key>`,
then `python -m plumbline.ai.evals` and commit `docs/results/ai/`.

Three things were tried and measured, and two were not shipped:

- **Meaning-based search for the agent**, offline (latent semantic analysis over the docs): the right
  section was already in the top 3 for 23 of 25 dev questions with keywords, and the meaning scores of
  answerable and unanswerable questions overlapped. The losses were in deciding and quoting, so the
  model reads the passages instead.
- **A learned identity re-scorer** (`identity/second_stage.py`): gradient boosting over Splink's
  candidate pairs, trained on one city and scored once on another. F1 0.9903 against Splink's 0.9912: not
  shipped. Of Splink's 82 missed pairs on that city, 47 were refused by the cluster rules and 7 were never
  compared; a re-scorer can reach only the 28 scored below the line. The lever is the cluster rules,
  and loosening them risks wrong merges.
- **A release gate for the home-buyer model** (`ml/release.py`, `docs/results/release_gate.json`). The
  champion is what production runs (logistic regression, chosen on validation; a first
  version wrongly hard-coded gradient boosting). On the 100,000-person test snapshot (30,323 people,
  660 buyers) it held gradient boosting (AUC -0.023, interval -0.037 to -0.009), the model
  without property data (AUC -0.050), and "balanced classes", whose AUC was the champion's to
  within 0.001 but whose probabilities were off by 0.44 on average. Agents read these scores as
  probabilities. On a 10,000-person city with 66 buyers the gate held everything, because the AUC
  intervals were too wide to call anything "not worse"; that is the gate working.

Two mistakes in my own harness, caught before any number was reported: the onboarding baseline once
listed words taken from the eval's partner headers (tuning on the test; removed), and the eval picked
a five-row CRM change file as a partner sample (now the largest delivery).

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

Recall by damage type (share of a record's true partners it was joined with): nickname 91.3%,
name typo 96.2%, missing zip 96.4%, missing phone 97.3%, address typo 97.8%, missing email 98.0%,
email typo 98.3%, undamaged 99.2%. The baseline gets 68 to 72% on missing or misspelled contact fields.
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

Remaining error class: family members (139 of the 149 mixed-up clusters are one household).

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

**What is left, and why.** 8 of 500 deleted people still have a record (3 in the CRM, 4 at the
vendor, 1 in the store). The kinds of leftover, measured in the first version of the world (11 left):
- 3 deleted people have records that never carried the email or phone in their request (a work
  email, blank fields). A request holding only those two hashes cannot reach them; the fix is upstream,
  in what the deletion intake collects.
- 8 moved: their old and new records share only the name. One key is not enough to delete, so name +
  zip single matches go to `deletion_review`: 2 persons, 1 of them a real leftover (7 before the
  phone rule below, so a reviewer's hit rate went from 1 in 7 to 1 in 2).
  Last name + address alone was tried too and dropped: 354 persons, 3 real, so a reviewer would drown.
- 15 store orders of housemates sharing an inbox are still erased at load: orders carry no phone, so
  at load time there is nothing to tell them apart. Fixing it means holding ambiguous rows until
  identity has run instead of erasing at once. Open.
- 0 innocent records (there was 1: two Jonathan Deckers at one address, both born in 1945).

**A rule that only scale revealed.** At 300,000 people, 8 people were deleted who never asked, and 45
of their records; at 1,000,000, 85 records. Every one shared a first name, last name and home with the
requester (a father and son), matched two tombstone keys, and had no date of birth to veto
it, because the requester's own rows, phone and all, had already been purged at load. Phones are
personal, and every record carrying the requester's phone is purged at load, so a person who still
has a phone of their own at this stage is someone else or the requester under a new number. Now:
two keys and another phone go to a person (`keys_match_but_phone_differs`), one key and another phone
is a namesake and is dropped. At 300,000 people: innocent people deleted 8 → 0, real deletions 58 →
58, deleted people with data left 15 → 15, review queue 54 → 14 persons. A test plants the father
and son.

That left one record at 1,000,000 people (85 → 1): the father had no phone either, so the phone rule
could not see him. His vendor record had no date of birth while his son's, from the purged row, was
known, and the vendor sends one. A missing date of birth in a source that sends one, against a
requester whose date of birth is known, now goes to a person (`keys_match_but_dob_unknown`): 1 → 0,
with no requester's record kept at any size (item 7 under [Scale](#scale)). A test plants this father
and son too.

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
| Current address | **91.1%** right | ceiling 91.9%: the other 8.1% moved and no source reported the new address |
| Address on a past date | 97.3% (2024-06-30), 95.3% (2025-03-31) | the true address on that date |
| Phone, date of birth, job title | 99.9%, 99.9%, 99.5% right when filled | |
| Work and personal email linked | **99.2%** right | of the 6,958 people for whom some source ever carried both |
| Moves used for segments | 91% precision, 69% of detectable moves found, a median 55 days after the move | 3,163 of 11,987 true moves were detectable at all |
| Households | precision 99.98%, recall 86.4% | true households (it was 97.8%: see below) |

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
"latest mention wins" by 0.5 points and "most trusted source wins" by 1.0. A small, consistent edge,
not a large one. Most of the remaining gap is the ceiling.

The gap between what is true and what a brand's own sources can see (only 26% of moves are detectable;
6,958 of 50,153 people ever show both a work and a personal email) is the size of the problem
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

On 2025-06-30 (30,366 people, 635 bought within 180 days):

| | AUC | Top-decile lift |
| --- | --- | --- |
| Random | 0.50 | 1.1x |
| Census ZIP income alone | 0.63 | 2.0x |
| **Plumbline model** (logistic regression, chosen on validation over gradient boosting) | **0.660** | **2.0x** |
| Same model without property and Census enrichment | 0.597 | 1.8x |
| What-if: plus an on-time vendor feed (age, renter, job change, income band as of each cutoff), 5-seed gradient-boosting ensemble | 0.714 | 2.8x |
| Ceiling: the rule that generated the purchases | 0.797 | 4.4x |
| Same model with vendor data pasted back from December | 0.836 | 3.7x |

How to read it:

- **Gradient-boosting numbers are ranges.** One fit is not reproducible: it adds up floating-point
  numbers in parallel, and the order of those additions is not fixed. On identical data (same data
  fingerprint) one fit gave 0.7179 on a laptop and 0.7249 in the cloud, and two runs on the same
  laptop gave different single-fit ranges (0.7188 to 0.7236, then 0.7178 to 0.7295). Gradient
  boosting is therefore always a 5-seed ensemble, and the card records the spread. In the first
  version of the world the ensemble stayed within 0.726 to 0.728 on every run (0.7259 and 0.7270 on the
  laptop, 0.7284 in the cloud); in the current world it scores 0.714. It shrinks the wobble, it does
  not remove it. Logistic regression wins on validation
  average precision by 0.0021 (0.0278 vs 0.0257), only 1.4 times gradient boosting's own seed spread
  (0.0015): in this world the choice is close, and a different world could flip it. Logistic
  regression is deterministic and gives the same AUC on every machine.

- **The leak beats the ceiling, which is how you know it is a leak.** Vendor attributes delivered in
  November and December say who owns a home after buying it. Pasted onto earlier snapshots they score
  above the best possible model, which only a model reading the future can do. `check_not_above_ceiling`
  refuses any backtest that does.
- **First-party data alone gets half the way.** The model captures 54% of the AUC above chance that the
  ceiling allows, and 31% of the lift. What drives buying here is household income, age, renting and a
  recent job change, which a brand's own systems barely see. An on-time vendor feed moves AUC from 0.66 to
  0.71: that difference is the price of enrichment, measured. The rest of the gap is exact household
  income, which nobody sells.
- **Calibration runs low.** Buying rose through 2025, so scores under-predict by about 1.3x in the top
  decile. Use scores to rank; the model card says so.

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
are not scored (33,426 on 2025-12-31): the model was trained before any vendor data existed, and they
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
| ZIP income alone | 79.8% | 0.91 | 0.47 |
| **Plumbline estimate** (5-seed gradient boosting) | **83.6%** | **0.83** | **0.57** |
| For comparison: the vendor's band, where it exists | 94.9% | | |

It is a modest gain, and the comparison row says why: what a brand sees about a person says little
about their income, and a bought band is much better. Estimates go to `ml.income_estimates` only if
they beat ZIP income alone, land within one band at least 60% of the time, and do not give everyone
the same band; otherwise nothing is published.

**Home seller: assessed, not published.** The question was who will sell their home in the next 180
days, among owners on the cutoff date. On validation the best candidate scored AUC 0.54; on the
test date 0.54, with a top-decile lift of 1.27. Even the rule that generated the moves reaches only
0.55: in this world selling is close to random among owners, so there is almost nothing to learn.
Two of the three gates failed (beats chance on validation, a ceiling that leaves room); the lift gate
passed, which with a ceiling of 0.55 is luck, and it took only one failed gate to stop publication.
No scores were written, and a stale `ml.home_seller_scores` table from an earlier run is
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
   exists: 57,374 inboxes, a half-point effect detectable at 80% power.
2. **Randomize the inbox, not the person.** My first design randomized people, and the answer key
   showed the flaw: families share inboxes, so 507 inboxes landed in both arms and "held out"
   people read the email their sibling was sent. Assignment is now a salted hash of the address.
3. **Attribute orders through the identity graph.** People check out with their work address, or
   mistype one. An order counts for an inbox if it was placed with that address or any address the
   identity graph ties to its people. 89.3% of orders in the window were attributed; the rest count
   as "did not buy" in both arms, which shrinks the effect toward zero. The readout also reports the
   effect divided by the attributed share, and says that correction assumes the loss is the same in
   both arms.
4. **Check before believing.** If the split does not match the design (sample ratio, alarm at
   p < 0.001, set in advance), the readout marks itself untrustworthy. A test breaks the split on
   purpose to prove it does.

**This run tripped the alarm.** In the current world the split came out 29,087 vs 28,287 inboxes,
p = 0.0008, and the readout refused to be trusted. I checked the hash before believing anything
else: over 200 other salts on these same inboxes, 3.5% of splits fall below p = 0.05 and none below
0.001, so the hash is fair and this salt is a one-in-a-thousand draw for this population. The
protocol written before the send says: re-send with a new salt. Swapping the salt after seeing the
result would be choosing the experiment by its outcome, so the numbers below are what this run
measured, shown as the refused readout they are:

| Bought within 30 days | Treatment | Holdout | Effect (95% CI) | True effect |
| --- | --- | --- | --- | --- |
| Everyone, as measured | 3.87% | 2.03% | +1.83 pts (+1.56 to +2.11) | +1.63 |
| Everyone, corrected for unattributed orders | | | +2.06 (+1.75 to +2.36) | +1.63 |
| Lapsed customers | 6.22% | 2.22% | +4.00 (+3.31 to +4.68) | +4.54 |
| Never ordered | 1.21% | 0.48% | +0.73 (+0.53 to +0.92) | +0.62 |
| Recent customers | 9.03% | 6.48% | +2.56 (+1.56 to +3.55) | +1.24 |

The recent-customer row overshoots, and the answer key says why: comparing the arms with perfect
attribution gives +2.68, while the email's real effect on those same inboxes was +1.22. The arms
differed in who was going to buy anyway, by luck of the draw, and nothing before the send showed it:
December purchases and order history were balanced between the arms (every p above 0.29). That is
the variance a confidence interval is about, not a bug, which is why the interval is reported across
runs rather than from one:

| Run | True effect | Measured (raw) | Corrected for attribution | Truth inside the corrected interval |
| --- | --- | --- | --- | --- |
| First world, 20,000 | +1.55 | +1.47 | +1.68 | yes |
| First world, 100,000 | +1.61 | +1.31 | +1.47 | yes (the raw interval missed) |
| First world, 300,000 | +1.63 | +1.45 | +1.63 | yes |
| First world, 1,000,000 (laptop) | +1.63 | +1.47 | +1.65 | yes |
| Current world, 20,000 | +1.60 | +1.57 | +1.81 | yes |
| **Current world, 100,000** (alarm: readout refused) | +1.63 | +1.83 | +2.06 | no (the raw interval held) |
| Current world, 300,000 | +1.63 | +1.55 | +1.74 | yes |
| Current world, 1,000,000 (laptop) | +1.63 | +1.51 | +1.69 | yes |

7 of 8 runs, as a 95% interval should roughly do. The one miss is also the one the readout refused to
vouch for.

CUPED (adjusting for each inbox's order history) narrows the interval by only about 1%: variance
falls by the squared correlation between history and outcome, and for a rare yes/no purchase that
is close to zero. A test pins that rule, on an outcome history does predict.

**Who to send it to next time.** An uplift model (two models, sent and held out; uplift is the
difference) learns who the email changes, using only what was known before the send. It is chosen
on one half of the inboxes and judged on the other: logistic regression beat gradient boosting on
validation Qini. Its ranking correlates 0.41 with the true effect (Spearman). Sending the next email
to 30% of the list:

| Policy | Extra buyers per 1,000 emails (from the answer key) |
| --- | --- |
| Send to everyone, or a random 30% | 16 |
| The 30% likeliest to buy | 24 |
| **The 30% the uplift model ranks highest** | **32** (39 at 300,000 and at 1,000,000; 20 at 20,000, where it loses to the likeliest buyers' 22) |
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
| Right section in the top 3 results | 45 of 49 answerable questions |
| Answered right, with the right source | 18 of 25 (dev half), **19 of 24 (test half)**; the refusal threshold (0.45) was chosen on the dev half only, and is re-tuned only when another beats it there by more than one question |
| Refused when the documents have no answer | 9 of 9 (dev), **7 of 9 (test)**: a 401(k) "match" and an "AWS account" each share enough words with a section to get through |
| Routed to the right skill and tool | 32 of 32 |
| Security questionnaire, drafted right with the right source | 20 of 20 (dev half), **13 of 20 (test half)**; 16 of 20 before the threshold moved (below) |
| Security questionnaire, left for a person when the controls page does not settle it | 7 of 7 (dev), **6 of 6 (test)** (certifications, audits, insurance, retention, incident timelines, a data protection officer) |

The eval sets were tripled (from 32 to 67 knowledge questions, 18 to 53 questionnaire items),
written before any of the new ones was run, and each expected answer was checked to exist in its
source section. Doing that found two grader bugs: YAML read `off` as false and `[33,287]` as two
numbers, and a right answer whose phrase wrapped across a line was graded wrong. With more dev
questions the knowledge threshold moved from 0.5 to 0.45: more right answers, and one more
unanswerable question answered. Each new section of documentation makes lexical refusal a little
worse (the lake section let the AWS question through); judging whether the passages actually answer
the question is the job model mode is for, and it is measured separately with your key.

The README is one of the documents the agent searches, so editing it moves the eval. A draft of the
app section above made 0.35 to 0.5 tie on the dev half; the rule then picked 0.5 and the held-out
half fell to 16 of 24. A later paragraph of this README moved it back. Both swings came from prose,
not from the agent, which is the clearest case for retrieval that scores meaning instead of shared
words. A test re-runs the choice on every change, so a stale threshold fails the build.

These numbers are for the offline mode, which quotes the best-matching bullets and table rows with
their source. What it misses: different words for the same thing ("runs the whole pipeline" vs "every
stage in order"), answers split across two sections, and a plausible but wrong section ranked first.
A language model reading the retrieved passages is what closes those; it is optional
(`PLUMBLINE_LLM_MODEL`, `PLUMBLINE_LLM_API_KEY`, any OpenAI-compatible endpoint) and its answers are
checked before anyone sees them: every sentence must cite a passage it was given, or the quoted
answer is shown instead. Measured with gpt-4.1-mini in [AI, measured](#ai-measured): the same 19 of 24 right on the
held-out half, and 9 of 9 refusals where quoting gets 7.

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
chosen on the dev half by the same rule as the knowledge eval (now 0.3). A test re-runs that choice
on the current documents and fails if the constant no longer matches, and it did: adding the
date-of-birth review case to the ingestion runbook made 0.25 and 0.3 tie on the dev half, the rule
picks the one nearer 0.5, and the held-out result fell from 16 of 20 to 13 of 20 (6 answerable items
now refused). I kept the rule rather than pick the threshold the test half likes: the lesson is
that lexical refusal sits on a knife edge, and a few paragraphs of documentation can move it. A
retrieval model that scores meaning instead of shared words is the real fix, and is not built.
Suffix stemming ("encrypted", "encryption") helped a little.

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

The same command at four sizes (`python scripts/scale_run.py --n-people N`), every stage from
generation to the experiment readout. Seconds per stage, and the most memory the process had used by
the end of the run:

| People | Machine | Rows delivered | Store sync | Ingest | Identity | dbt products | Models | Experiment | Whole pipeline | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20,000 | 2-core cloud, 7 GB | 62,679 | 6 | 7 | 24 | 21 | 102 | 7 | 2.9 min | 2.4 GB |
| 300,000 | 2-core cloud, 7 GB | 945,970 | 99 | 93 | 97 | 86 | 413 | 35 | 15.1 min | 3.1 GB |
| 1,000,000 | 10-core laptop | 3,150,929 | 350 | 87 | 84 | 64 | 393 | 25 | 18.0 min | 5.0 GB |

And whether the answers hold as the world grows (current world; every row a full run):

| People | Identity precision / recall | Deleted people with data left | People deleted who never asked | Current address right (ceiling) | Households F1 | Home-buyer AUC (ceiling) |
| --- | --- | --- | --- | --- | --- | --- |
| 20,000 | 99.6% / 98.8% | 0 of 100 | 0 | 91.2% (92.0%) | 0.931 | 0.638 (0.783) |
| 100,000 | 99.4% / 98.8% | 8 of 500 | 0 | 91.1% (91.9%) | 0.927 | 0.660 (0.797) |
| 300,000 | 99.4% / 98.8% | 19 of 1,500 | 0 | 91.2% (92.0%) | 0.928 | 0.653 (0.800) |
| 1,000,000 | 99.3% / 98.8% | 66 of 5,000 | 1 record, then 0 (item 7) | 91.1% (91.9%) | 0.928 | 0.652 (0.792) |

What scale found, in order, each fixed at its root and held by a test:

1. **Identity got worse as the world grew** (precision 99.1%, 97.4%, 93.5%): namesakes multiply
   faster than people, and a too-generous email-typo rule joined them. Fixed in the matcher (item 5
   under identity); precision is now flat.
2. **The store sync was quadratic**, but in my test server, not the connector: the mock API re-sorted
   every order for every page, 537 s at 300,000 people. It now keeps a sorted index, like the
   database a real store pages on, and a test proves it returns exactly what the slow version did.
   Most of what remains is honoring `Retry-After` on throttled requests, which a real store imposes too.
3. **Memory is DuckDB's buffer cache more than the data.** Identity jumps to 2.4 GB already at
   20,000 people because DuckDB takes memory it is allowed to; it grows to 5.0 GB at 1,000,000 (4.2 GB
   in the run before one household per home; I have not measured why it moved). Splink's own DuckDB
   is most of it: on 3,741 records identity peaked at 2.2 GB, and with `PLUMBLINE_DUCKDB_MEMORY=600MB`
   at 0.8 GB, 3 seconds slower, with the same persons (`test_a_memory_cap_changes_how_much_memory_identity_uses_not_who_is_who`).
4. **1,000,000 people broke two assumptions.** Scoring crashed on two people the store data cannot
   tell apart (item 6 under identity). Then the dbt test `every_address_spelling_has_one_canonical`
   failed in a feature snapshot: address spellings are merged when they look alike, but alike is not
   transitive ("1901 DORCHESTERROAD APT 26" is like "1901 DORCHESTER ROAD APT 26", which is like
   "1901 DORCHESTER AVE", which the first is not like). Pointers are now followed to their end; where
   no chain exists, nothing changes. On the 2-core, 7 GB machine the 1,000,000 run is killed for
   memory in identity; with `PLUMBLINE_DUCKDB_MEMORY=3GB` it completes identity (467 s) and deletion,
   with the same persons deleted and queued as on the laptop.
5. **Deletion started deleting the wrong people.** Records of people who never asked, deleted: 0, 1,
   45, 85 at 20,000, 100,000, 300,000 and 1,000,000 people, and the review queue 1, 7, 107, 1,097
   persons. Each was a namesake in the requester's home with no date of birth to tell them apart. A
   person who still has a phone of their own after the requester's rows were purged is someone else,
   so they now go to a person or are dropped (see "A rule that only scale revealed"). Measured on the
   same warehouses: 45 to 0 at 300,000, with nobody left behind who was not before.
6. **Households were being merged by the generator, not the matcher.** Household precision fell
   from 97.8% to 93.6% between 100,000 and 300,000 people. 85% of the merged households were two
   families the answer key itself put in one flat: the generator drew each apartment at random with no
   record of who lived where (the birthday problem). Each home now holds one household; household
   precision is 99.98% and F1 is flat with size. Every number in this README is from that corrected
   world.
7. **One wrongly deleted record was left at 1,000,000 people**, after the phone rule took 85 to 1: a
   father's vendor record (no email, phone or date of birth) at the home of his son of the same name,
   who asked. Name + zip and last name + address are two keys, but for two people in one home they are
   one fact said twice; the date of birth tells them apart, and the son's was known while the vendor,
   which sends one, sent none for this record. Such a match now goes to a person
   (`keys_match_but_dob_unknown`). Re-planned on the saved pre-deletion state of every run: 1 to 0 at
   1,000,000, review 92 to 93 persons, no requester's record kept that was deleted before, and nothing
   changes at 20,000 to 300,000. The rule was found on that run, so it is not its own independent check.
8. **Every dbt build left a 2 MB folder in the temp directory**, and nothing removed them: 909 on the
   test machine. A passing build now removes its folder; a failing one keeps it, and the error names it.

Nothing here claims terabytes: everything runs on one machine.

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
python -m pip install -e ".[dev,orchestration,lake,app]"
python -m pytest -q                                      # use python -m, so the venv's pytest runs
export CENSUS_API_KEY=...                                # free: api.census.gov/data/key_signup.html
python scripts/fetch_reference.py --out data/reference  # once; needs internet; safe to rerun
python -m plumbline.synth.cli --ref data/reference --out data/synth --n-people 100000 --seed 7
python -m plumbline --score                              # every stage in order, with accuracy receipts
# a world made by an older generator is refused; regenerate it with the same command plus --force
python -m plumbline.agents ask "did last night's load reject anything?"
python -m plumbline.agents eval                          # knowledge, routing (and the model, if configured)
dagster dev -m plumbline.ingest.orchestration            # optional: the same pipeline, with checks, in a UI
python -m streamlit run streamlit_app.py                 # the demo app (python -m: always the venv's Streamlit)
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
docker compose up app                         # the demo app on :8501 (localhost only)
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
