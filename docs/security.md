# Security and privacy controls

What Plumbline does, stated so it can be checked. Where a control is missing, this says so: a
questionnaire answered from this page must be as true as the page.

## Personal data held

The warehouse (`data/warehouse.duckdb`) holds consumer records from four sources: names, postal
addresses, emails, phone numbers, dates of birth and order history. All of it is synthetic in this
project; emails use reserved `.test` and `.example` domains and phones the fictional 555 area code.
Property owner names from PLUTO are never loaded: the loader refuses any `owner` column.

## Deletion requests

A deletion request names an email hash and a phone hash. Matching rows are purged from storage and
quarantine and blocked in every later load, including replays of old files. After identity
resolution, the person's other records are deleted too, and cases with too little evidence go to a
review queue decided by a person with the privacy role. Measured on the seed-7 run: 11 of 500
deleted people still have some record, each with a known reason in the README.

## Unsubscribes and contact suppression

An unsubscribe or a deletion request blocks the address for marketing in every audience, including
audiences rebuilt for past dates. A dbt test fails the build if any audience would email one.

## Who can change data

Nobody changes production data through the agent without approval. A write is a proposal until a
second person who holds the required role approves it; the person who asked cannot approve it. Roles
are granted the same way, by an admin who is not the person asking, and every grant and removal is
kept in an access record. Read tools open the warehouse read-only.

## Audit trail

Every question, tool call, proposal, approval, refusal and role change is in `ops.audit` in the agent
database, with who did it and when. Arguments are stored as SHA-256 hashes, not in clear, so the log
is not a second copy of personal data.

## Secrets

API keys and tokens (the store API, Slack, the language model) are read from environment variables,
never written to files, logs or the warehouse, and never shown in a printed object. A test checks
that the store API token reaches no file. `.env` files are git-ignored.

## Personal data in logs and agent answers

Agent tools return person keys at most, never names, emails or phones; a test checks every tool's
output. Slack request logging is off, because request lines can carry tokens.

## Encryption

Plumbline does not encrypt data at rest itself. The warehouse and agent database are plain DuckDB
files; encryption at rest depends on the disk they are stored on. Traffic to Slack and to model
endpoints uses HTTPS; the local mock store API is plain HTTP on the loopback interface only.

## Access to the machine

Plumbline has no user accounts or single sign-on of its own. Anyone with access to the machine and
its files can read the warehouse. Access control for people is the operating system's.

## Recovery

Every table can be rebuilt from the raw delivered files; the ingestion runbook describes it, and a
test checks that a rebuild is identical. There are no automated backups of the raw files.

## Change control

Every change runs the full test suite and the dbt tests. The guardrails in this page are each held by
a test, and each test was checked by breaking the guardrail on purpose to see that the test fails.

## Copies in the data lake

When bronze is published to the Iceberg lake, a deletion in the warehouse is carried into the lake
on the next publish: the rows are deleted, older snapshots are expired, and data files and manifests
no snapshot needs are removed from the bucket. Lake tables store counts in their statistics, never
column values. The lake is only as current as the last publish.

## Third-party data

Vendor data is used as delivered. Plumbline does not resell or share data with other parties; it
sends data to nothing outside the machine except Slack messages and, if configured, model prompts
that contain retrieved document passages, not consumer records.
