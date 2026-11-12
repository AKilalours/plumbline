# Postmortem: both feeds failed when the cloud warehouse refused a newer DuckDB

**Date:** 2026-11-07 · **Duration:** 42 minutes (07:17 to 07:59 UTC) · **Severity:** both real feeds down
**Status:** resolved; all actions done · Blameless: about the system, not the person.

## Summary

The daily jobs installed DuckDB without a version pin. DuckDB 2.0 was released, the runner picked it up, and
the MotherDuck extension (which publishes to the cloud warehouse) supported DuckDB up to 1.5.6 only. Publishing
raised `InvalidInputException: Initialization function "motherduck_duckdb_cpp_init" ...`, and because the publish
ran inside the sync's success path, the whole run failed and the source's cursor was not saved.

## Impact

- 2 failed runs of each feed (NYC permits at 07:17 and 07:40 UTC; the Wikimedia stream at 07:17 and 07:40 UTC).
- No data lost: the cursor was not saved, so the next run re-read from the last good position.
- Each feed's 28-day "runs succeed" error budget (95%) was spent on its first day: 24 of 26 and 23 of 25 runs
  have succeeded since ([SLOs](../reliability/slo.md)).

## Detection

Automatic, in 2 minutes: the alert step in the daily job opened GitHub issues #1 and #2 at 07:19:37 UTC
("the latest run failed", with the error). Both closed themselves at 08:02 UTC, when the next runs succeeded.

## Timeline (UTC)

| Time | Event |
|---|---|
| 07:17 | NYC and stream runs fail at publish; nothing recorded as synced |
| 07:19 | Issues #1 and #2 opened by the alert step |
| 07:40 | A rerun fails the same way: confirms it is not transient |
| ~07:50 | Cause found: DuckDB 2.0 on the runner; MotherDuck supports ≤ 1.5.6 |
| 07:59 | Runs with `duckdb==1.5.6` pinned succeed |
| 08:02 | Issues closed as recovered |

## Root cause

1. **An unpinned dependency in a scheduled job.** A release outside this project changed what ran.
2. **A failure in an optional step failed the required one.** Publishing to the cloud copy shared a success path
   with the sync, so a publish problem lost the sync's cursor too.

## What went well, what did not

- Well: detection and the issue trail were automatic, with the real error message.
- Not well: the job's dependencies were whatever pip resolved that morning.

## Actions (all done)

| Action | Where | Proof |
|---|---|---|
| Pin `duckdb==1.5.6` in the daily job, the version MotherDuck supports | `.github/workflows/open_data.yml` | every run since 07:59 |
| A publish failure is recorded as `publish_error`; the sync's cursor is kept; the job still turns red | `ingest/open_data_daily.py`, `ingest/stream_daily.py` | tests that fail on the old code (`tests/test_open_data_daily.py`, `tests/test_stream_daily.py`) |
| A failed publish is its own alert ("the cloud publish failed") and spends the runs budget | `ingest/alerts.py`, `ingest/slo.py` | `tests/test_alerts.py`, `tests/test_slo.py` |
