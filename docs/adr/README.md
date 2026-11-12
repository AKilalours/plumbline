# Architecture decisions

One page per decision that shapes the platform: the context, what was chosen, what was rejected and why, what
it costs, the evidence, and the condition that would change it. Each names the test that keeps the code honest
to it. Written as the project went; dated when the decision was made or last changed.

| # | Decision | Revisit when |
| --- | --- | --- |
| [0001](0001-one-machine-warehouse.md) | DuckDB on one machine, MotherDuck as the cloud copy, Spark only where it proves a rule | a working set past one machine's disk, or many writers at once |
| [0002](0002-land-then-confirm.md) | Land, then confirm, for every source that keeps a position | a source that cannot replay (then: an outbox) |
| [0003](0003-reviewed-contracts.md) | Every source has a reviewed contract; nothing loads on a guess | contracts that change faster than people can review them |
| [0004](0004-reloads-and-reconcile.md) | Skip a publisher reload, then reconcile what is held against the source | the reconcile finds real changes inside reloads |
| [0005](0005-freshness-against-the-source.md) | Lag is measured against the source's newest update, not the clock | a source with no "newest update" to ask for |
| [0006](0006-cdc-with-test-decoding.md) | Postgres change capture with test_decoding and peek/advance, not a streaming protocol | sustained load near the measured ceiling, or sub-second latency |
| [0007](0007-synthetic-people-real-places.md) | Invented people with an answer key, real public data for places and feeds | access to real consented data with ground truth |
