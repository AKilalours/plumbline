# What each part of Plumbline demonstrates

Plumbline was built against the work described in four data roles at an AI-for-consumer-marketing
company: data products, ingestion, data science, and internal AI systems. This page maps that work
to code and to the measurement that backs it. Gaps are listed as gaps.

## Data products: turning messy consumer data into trusted attributes

| The work | Where | Evidence |
| --- | --- | --- |
| Establish grain, keys, coverage and failure modes of messy multi-source data | `sources/*.yaml`, `docs/synthetic_data_card.md` | contracts per source; damage measured per kind |
| Identity resolution and entity relationships | `src/plumbline/identity/` | F1 0.991 vs 0.927 baseline, holding at 0.991 at 300,000 people; recall by damage type; a scale-dependent namesake bug found and fixed |
| Durable domain models and derived attributes | `dbt/models/marts/` | golden person, address history, households, life events |
| Quality checks, provenance and guardrails that tell reliable signal from misleading data | `dbt/models/marts/fct_life_events.sql`, `int_attribute_winners.sql` | vendor-only moves measured at 45% precision and kept out of segments |
| Point-in-time attributes | `dim_person_address_history`, `as_of` var | 97.3% right on 2024-06-30; test that a past build uses nothing later |
| dbt inside Dagster | `src/plumbline/ingest/orchestration.py` (`data_products` asset) | `test_materialize_runs_checks_and_they_pass` |
| Outputs safe to consume autonomously | `mart_activation_audience`, dbt singular tests | compliance test that fails if the unsubscribe rule is removed |
| Show a non-engineer what the data says, and let them check it | `streamlit_app.py`, `src/plumbline/app/` | a live city built by the real pipeline; every person, household and deletion shown next to the answer key; pages tested without a browser and drawn in one |
| Let people ask the data products questions in plain English, safely | `ai/ask_data.py`, `evals/ask_data.yaml` | the model writes SQL; parsed before it runs: one SELECT, data products only, a column allow-list with no contact data, no file readers; graded on result values against gold queries |

## Ingestion: making sources you do not control dependable

| The work | Where | Evidence |
| --- | --- | --- |
| APIs with rate limits, files that arrive late and get corrected, schemas that drift | `ingest/connectors/store_api.py`, `ingest/engine.py` | 63 failures survived at full scale; v2 supersedes v1; renamed columns |
| Reruns, backfills and retries that never duplicate | file ledger, row hashes, `delivery_rows` | 100 random faults change nothing; rerun is a no-op |
| Bad records quarantined, not loaded | `quarantine` with reason codes | `test_bad_rows_go_to_quarantine_with_reasons` |
| Onboarding by configuration | one YAML per source | `test_new_source_onboards_with_yaml_only` |
| Onboard a partner file nobody described | `ai/onboard.py`, `evals/onboarding.yaml` | a drafted contract (types, email and phone flags, identity roles, key) checked by the same loader, written to `sources/drafts/` for a person; scored on held-out partner files against rules without a model |
| Origin of every value | `_first_delivery_id`, `delivery_rows`, provenance columns in `dim_person` | each golden value names its source and date |
| Delete whole persons without deleting their namesakes, at any size | `privacy/person_deletion.py` | records of people who never asked: 0 at 20,000 to 300,000, and at 1,000,000 85 → 1 (phone rule) → 0 (date-of-birth rule, re-planned on the same state); review queue kept human-sized |
| Restricted data cannot return through a new load or a replay | `ingest/erasure.py`, `privacy/` | replay tests at row and person level |
| Recovery runbooks | `docs/runbooks/ingestion.md` | |
| Open table formats on object storage | `lake/iceberg.py` (Iceberg on any S3 API) | sync with snapshots and time travel; deletion expires history and removes files; no column values in manifest statistics; tested against an S3 API |

## Data science: features and models agents can rely on

| The work | Where | Status |
| --- | --- | --- |
| Features from raw signal, including property data | `dbt/models/marts/fct_person_features.sql` (PLUTO, Census, store, life events) | point in time per cutoff |
| Home buyer model | `src/plumbline/ml/` | AUC 0.660 vs ceiling 0.797 on an untouched later period; enrichment worth +0.06, an on-time vendor feed +0.05 |
| Feature pipelines that are reliable in production | snapshots by `as_of`, leak guards, window rule | three leaks found and turned into tests |
| Validations so agents can act without a human in the loop | `ml/guardrails.py`, `ml.model_runs` | scores published only if every check passes; out-of-population people not scored |
| Decide whether a new model replaces the current one | `ml/release.py`, `docs/results/release_gate.json` | paired-bootstrap AUC, calibration, top decile and slices; on 100,000 people it held a challenger with the champion's AUC and probabilities off by 0.44 |
| Try a learned model where rules work, and say when it does not help | `identity/second_stage.py` | trained on one city, scored on another: no gain over Splink, with the reason (most misses are refused by cluster rules); not shipped |
| Income estimates where the vendor has nothing | `ml/income.py`, `ml.income_estimates` | within one band 83.6% vs 79.8% for ZIP income alone, on people the vendor never covered; vendor columns never features; households kept whole across the split |
| Measure what a campaign caused, and whom to target | `experiments/`, `exp.plan`, `exp.readouts` | inbox-level holdout, sample-ratio alarm that refused one unlucky run, CUPED, Holm; true effect inside the corrected interval in 7 of 8 runs; uplift targeting 32 to 39 extra buyers per 1,000 emails vs 24 for likeliest buyers |
| Home seller model, and knowing when not to ship | `ml/seller.py`, `ml.model_runs_seller` | assessed and refused: validation AUC 0.54, ceiling 0.55; stale scores dropped |

## Internal AI systems

| The work | Where | Evidence |
| --- | --- | --- |
| A knowledge agent that answers with sources and that people trust | `agents/knowledge.py`, `agents/answer.py` | cites every claim; on 67 eval questions, 19 of 24 right on the held-out half and 7 of 9 unanswerable ones refused; README numbers checked by a test against the live eval |
| A human approval before any agent writes to a system of record | `agents/approvals.py` | second person with the role; runs once; `test_a_write_needs_a_second_person_with_the_role` |
| A model picks the tool; code decides if it may run | `ai/planner.py`, `evals/routing_hard.yaml` | typed tool calls; ids must appear in the message, roles must exist, writes stay proposals; 40 hard messages scored against keyword rules |
| Model calls that can be audited and replayed | `ai/runtime.py`, `docs/results/ai/` | every call recorded by prompt hash; tests and the public app replay real outputs without a key |
| Audit trails that double as compliance evidence | `agents/audit.py` | every step logged, arguments hashed |
| Skill library: owner, eval, permissions per skill | `skills/*.yaml`, `agents/skills.py` | bad skills refused at load |
| Own the source of truth; the agent proposes the fix, a person approves | `docs/claims.yaml`, `docs_freshness` skill | found and fixed a stale runbook passage |
| Webhooks and their failure modes | `agents/slack.py` | signature, replay window, retry and double-click dedupe, bot loops |
| Measure adoption and cost; retire what nobody opens | `agents/usage.py` | per skill: requests, refusals, people, tokens, cost, hours retired (measured uses times an owner's baseline); unused skills |
| Access provisioning with an access record that writes itself | `agents/access.py`, `skills/access_admin.yaml`, `ops.access` | grant, remove, offboard only by an admin who neither asked nor is the person; last admin cannot be removed; `tests/test_access.py` |
| Security questionnaires answered from documented controls | `docs/security.md`, `agents/questionnaire.py` | on 53 items, 13 of 20 drafted right on the held-out half (16 before a runbook change moved the dev-chosen threshold), 6 of 6 controls we lack left for a person |
| Runbooks people follow | `docs/runbooks/agent.md` | |
| An internal tool people can use without the command line | `streamlit_app.py` (Ask the agent) | the same agent, approvals and audit behind a chat page; each viewer gets their own copy of the warehouse; no model key on a public page |

Not built: renewals, and connections to a real identity provider or HR system (offboarding here
changes the agent's own roles, not accounts in other tools). No real users: hours retired is a
mechanism with owner-set baselines, not a measured saving.

## Not covered, by choice so far

Snowflake, Spark, Iceberg, Trino and AWS: everything runs on DuckDB on one machine. The design (file
ledger, contracts, dbt models) carries over; the claim that it scales to terabytes is not made here.
