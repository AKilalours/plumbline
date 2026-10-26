# What each part of Plumbline demonstrates

Plumbline was built against the work described in four data roles at an AI-for-consumer-marketing
company: data products, ingestion, data science, and internal AI systems. This page maps that work
to code and to the measurement that backs it. Gaps are listed as gaps.

## Data products: turning messy consumer data into trusted attributes

| The work | Where | Evidence |
| --- | --- | --- |
| Establish grain, keys, coverage and failure modes of messy multi-source data | `sources/*.yaml`, `docs/synthetic_data_card.md` | contracts per source; damage measured per kind |
| Identity resolution and entity relationships | `src/plumbline/identity/` | F1 0.981 vs 0.927 baseline; recall by damage type |
| Durable domain models and derived attributes | `dbt/models/marts/` | golden person, address history, households, life events |
| Quality checks, provenance and guardrails that tell reliable signal from misleading data | `dbt/models/marts/fct_life_events.sql`, `int_attribute_winners.sql` | vendor-only moves measured at 45% precision and kept out of segments |
| Point-in-time attributes | `dim_person_address_history`, `as_of` var | 97.0% right on 2024-06-30; test that a past build uses nothing later |
| dbt inside Dagster | `src/plumbline/ingest/orchestration.py` (`data_products` asset) | `test_materialize_runs_checks_and_they_pass` |
| Outputs safe to consume autonomously | `mart_activation_audience`, dbt singular tests | compliance test that fails if the unsubscribe rule is removed |

## Ingestion: making sources you do not control dependable

| The work | Where | Evidence |
| --- | --- | --- |
| APIs with rate limits, files that arrive late and get corrected, schemas that drift | `ingest/connectors/store_api.py`, `ingest/engine.py` | 63 failures survived at full scale; v2 supersedes v1; renamed columns |
| Reruns, backfills and retries that never duplicate | file ledger, row hashes, `delivery_rows` | 100 random faults change nothing; rerun is a no-op |
| Bad records quarantined, not loaded | `quarantine` with reason codes | `test_bad_rows_go_to_quarantine_with_reasons` |
| Onboarding by configuration | one YAML per source | `test_new_source_onboards_with_yaml_only` |
| Origin of every value | `_first_delivery_id`, `delivery_rows`, provenance columns in `dim_person` | each golden value names its source and date |
| Restricted data cannot return through a new load or a replay | `ingest/erasure.py`, `privacy/` | replay tests at row and person level |
| Recovery runbooks | `docs/runbooks/ingestion.md` | |

## Data science: features and models agents can rely on

| The work | Where | Status |
| --- | --- | --- |
| Features from raw signal, including property data | `dbt/models/marts/fct_person_features.sql` (PLUTO, Census, store, life events) | point in time per cutoff |
| Home buyer model | `src/plumbline/ml/` | AUC 0.659 vs ceiling 0.806 on an untouched later period; enrichment worth +0.05, an on-time vendor feed +0.08 |
| Feature pipelines that are reliable in production | snapshots by `as_of`, leak guards, window rule | three leaks found and turned into tests |
| Validations so agents can act without a human in the loop | `ml/guardrails.py`, `ml.model_runs` | scores published only if every check passes; out-of-population people not scored |

## Internal AI systems

| The work | Status |
| --- | --- |
| Agents with sources, approvals before writes, audit trails | Phase 4, not started |

## Not covered, by choice so far

Home seller and income models: the same feature pipeline would carry them; not built.
Snowflake, Spark, Iceberg, Trino and AWS: everything runs on DuckDB on one machine. The design (file
ledger, contracts, dbt models) carries over; the claim that it scales to terabytes is not made here.
