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

**What the tests are worth:** I broke six promises in the loader one at a time (no version
replacement, no row dedup, two deletion checks, no missing-column rejection, no file ledger).
Four were caught at once. One exposed a missing test (a corrected file that retracts rows), which
is now added and catches it. One survived because the check was redundant, so it was removed.

**Known limit, measured:** deletion matching uses exact email and phone. On the seed-7 run, 8
of 500 deleted people still have a record (3 CRM, 5 vendor) because a source misspelled their
email and had no matching phone. Phase 2 identity matching is meant to close this by deleting at the
person level; this number is the before.

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

Details and the reasons behind each choice are in `src/plumbline/ingest/engine.py`.
Recovery steps are in [the runbook](docs/runbooks/ingestion.md).

## Run it

```bash
python -m pip install -e ".[dev,orchestration]"
python -m pytest -q                                      # use python -m, so the venv's pytest runs
export CENSUS_API_KEY=...                                # free: api.census.gov/data/key_signup.html
python scripts/fetch_reference.py --out data/reference  # once; needs internet; safe to rerun
python -m plumbline.synth.cli --ref data/reference --out data/synth --n-people 100000 --seed 7
python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb
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
