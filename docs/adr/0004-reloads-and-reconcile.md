# 0004 · Skip a publisher reload, then reconcile what is held against the source

*2026-11-12, after the NYC incident*

**Context.** On 2026-11-10 NYC re-stamped 803,457 permits with one update time. The incremental sync stopped at
its page budget inside that block and restarted at its top every run; seven runs passed while 2,438 real changes
after it never landed.

**Decision.** A run stopped by its budget keeps its cursor. A block of more than 100,000 rows under one update
time is counted with one request, recorded as a skipped reload, and passed, so later changes land in the same
run. Separately, every run re-asks the source for the next 2,000 rows the warehouse holds and compares them
field by field; changed rows are reloaded, rows the source no longer has are reported (never deleted on that
alone), and more than 1% gone opens an alert.

**Rejected.** Re-reading the whole block: 800 pages, hours per run, days of staleness, to learn mostly nothing.
A full resync on a schedule: the same cost every time, and it hides a reload instead of naming it. Trusting
update times alone: they missed a reload, and they can miss a change made without moving the time.

**Costs.** Between a reload and the reconcile reaching a row, a change hidden in the reload is not in the
warehouse; a full pass takes held / 2,000 runs. The reconcile looks rows up by permit number, because NYC's API
refuses `=` and `in` on its row id (checked against the real API); a row whose permit number changed is reported
as gone.

**Evidence.** The first run after the fix skipped 803,457 rows, landed exactly the 2,438 changes after them
(counted independently on the API), was 0 hours behind and took 42 seconds instead of 5 to 15 minutes.
`tests/test_socrata.py`, `tests/test_open_data_daily.py` (reload, budget, reconcile: each fails on the old code).

**Revisit when** the reconcile finds real changes inside reloads often: then reloads should be re-read, slowly,
in the background.
