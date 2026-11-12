# 0002 · Land, then confirm, for every source that keeps a position

*2026-10, applied to each new source since*

**Context.** Five sources keep a position for us: the store API's cursor, Socrata's keyset cursor, the SSE
stream's Last-Event-ID, Kafka's committed offsets, and a Postgres replication slot. A job can die between
writing data and saving the position.

**Decision.** Always write the batch first (atomically: a temporary file, then a rename), and only then move the
position. A crash in between re-reads the same batch (at least once), and the loader keeps one copy because
every row has a stable key and a content hash (effectively once).

**Rejected.** Position first, then data: a crash loses the batch, silently. Exactly-once transactions across
the source and the warehouse: no common transaction exists between an HTTP API, a file system and DuckDB, and
two-phase commit would be the most fragile code here.

**Costs.** Duplicates on disk after a crash, which the loader must always absorb; the loader therefore never
trusts a delivery to be new.

**Evidence.** A crash test per source: `tests/test_store.py`, `tests/test_socrata.py`,
`tests/test_stream.py`, `tests/test_kafka.py` (against a real broker), `tests/test_postgres_cdc.py` (against
a real Postgres): each kills the job between the two steps and checks nothing is lost or doubled.

**Revisit when** a source cannot replay what it sent; then it needs an outbox on its side, not a change here.
