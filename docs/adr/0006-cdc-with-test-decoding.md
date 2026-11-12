# 0006 · Postgres change capture with test_decoding and peek/advance

*2026-11-12*

**Context.** A company's first-party data lives in its own database. Copying tables again on a schedule misses
deletes, loads the database, and cannot tell the order of changes.

**Decision.** A logical replication slot, created with an exported snapshot; the starting copy is read inside that
snapshot, so it ends where the log begins. Changes are read with `pg_logical_slot_peek_changes` (not consumed),
landed, then confirmed with `pg_replication_slot_advance`. Decoded by `test_decoding`, which ships with
Postgres. Each change carries its log position; the newest wins; a delete as the newest change removes the row.

**Rejected.** The pgoutput plugin over the streaming replication protocol (what Debezium uses): lower latency and
binary decoding, but a long-lived connection, keepalives and feedback messages to manage, and at-least-once is
harder to see. wal2json: an extension to install, which managed databases do not always allow. Polling
`updated_at`: misses deletes and same-time changes.

**Costs.** Polling latency (as often as the job runs). Text decoding is the ceiling: 64,000 to 76,000 changes a second
over two runs on a 2-core machine (`docs/results/postgres_cdc_bench.json`). A batch is whole transactions, so one huge
transaction is one huge batch. A slot nobody reads keeps the database's log on disk (16 MB for a 100,000-change
burst; reported as lag). Large values an update leaves alone are not in the log unless the table logs full rows;
they are then read back from the table by primary key, which is exact for the current view but may show a later
value in that one history row (`REPLICA IDENTITY FULL` avoids it).

**Evidence.** `tests/test_postgres_cdc.py` against a real Postgres 16 on every push: a day of changes ends equal to
the database row for row; a crash between landing and confirming loses and doubles nothing; a change made during
the starting copy arrives once; a 14 kB note survives an update that does not touch it, under both identities.

**Revisit when** sustained change rates approach the measured ceiling, or consumers need sub-second latency: move
to pgoutput streaming (or run Debezium) and keep the contracts, the log-position ordering and the delete rule.
