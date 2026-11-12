# 0001 · DuckDB on one machine; MotherDuck as the cloud copy; Spark only to prove a rule

*2026-10 (MotherDuck added 2026-11-07)*

**Context.** The workload is many small, messy deliveries (files, API pages, stream batches, change logs) that
must load exactly once, plus dbt models and identity resolution over a few million records. A team of one has to
run, test and debug all of it on every push.

**Decision.** DuckDB is the warehouse: the loader, dbt and the matcher run in one process on one file. Published
products are upserted into MotherDuck (DuckDB's managed service) so they live off the laptop and survive a job's
container. Spark appears once, to show the loader's "current version" rule gives the same 282,784 rows on Spark.

**Rejected.** Snowflake or BigQuery: a bill and a network hop on every test, and nothing here needs their
concurrency. A Spark cluster: the data fits in memory many times over; cluster time would be spent on
orchestration, not on the hard parts (contracts, identity, deletion). Postgres as the warehouse: row storage
for column-heavy models.

**Costs.** One writer at a time; no fine-grained warehouse permissions; claims stop at the measured scale.

**Evidence.** The whole pipeline at 1,000,000 people in 18 minutes on a 10-core laptop (5.0 GB peak); 300,000 in
15 minutes on a 2-core runner; a projection, labelled as one, puts one 2-core machine at 384 hours for 547 million
people (Models → Scale). `tests/test_spark.py` (row-for-row parity, and two deliberately broken rules caught).

**Revisit when** the working set passes one machine's disk, or several jobs must write the same tables at once.
The contracts, the ledger and the dbt models move as they are; the loader's SQL is the part to port, and the
Spark parity test is where to start.
