# Postmortem: NYC runs passed while the data stopped moving

**Date:** 2026-11-10 19:43 to 2026-11-12 04:36 UTC · **Severity:** stale data, no failed run · **Status:**
resolved; actions done; one known limit · Blameless.

## Summary

At 19:43:03.479 on 2026-11-10 NYC Open Data re-stamped 803,457 rows of the permits table (rbx6-tga4) with one
update time: the table was republished, not 800,000 rows changed (counted on the API afterwards). Every sync
after that read its 60-page budget, all inside that one block, and stopped. A run stopped by its budget threw
its cursor away, so the next run began again at the top of the block. Real changes published after the block
never landed, and every run reported success.

## Impact

- **2,438 real permit changes**, published by the city at 2026-11-11 19:49 UTC, landed 8.8 hours late (at
  2026-11-12 04:36 UTC, the first run after the fix).
- The landed data was at most 24.1 hours behind the city's newest update: inside the 26-hour freshness
  promise, so no SLO was breached. Measured by the new lag-against-source check; at the time, nothing measured it.
- Runs took 5 to 15 minutes instead of about 20 seconds, re-reading 60,000 rows each to find 0 to 20 new ones.

## Detection

**Manual, not by monitoring.** Found on 2026-11-12 around 04:00 UTC during a review of the run history: the
"hours behind the source" number climbed run after run (3.9, 11.1, 21.6, 29.2) while every run passed. Monitoring
could not see it, because that number was *now minus the newest row landed*, which cannot tell "the city
published nothing" from "we are stuck", and the feed status only asked whether a run had succeeded.

## Timeline (UTC)

| Time | Event |
|---|---|
| 11-10 19:43 | NYC re-stamps 803,457 rows with one update time |
| 11-10 23:34 | First run inside the block: 60 pages, 58,540 rows, 692 s; stops at its budget, cursor dropped |
| 11-11 06:48, 17:18; 11-12 00:52 | Same 60 pages each run; 0 to 1,460 rows new; 321 to 942 s |
| 11-11 19:49 | NYC publishes 2,438 real changes after the block |
| 11-12 ~04:00 | Found in review; block size counted on the API (`count(*)` at that update time) |
| 11-12 04:36 | First run with the fix: reload skipped (803,457 rows, recorded), 2,438 rows landed, 0 h behind, 42 s |
| 11-12 13:35 | First reconcile slice: 2,000 held rows checked against NYC |

## Root cause

1. **A budget stop was treated as the end of the data.** "Resumable" covered crashes, not the page budget: the
   code marked a capped run done and dropped its cursor.
2. **Lag was measured against the clock, not the source.** So a stuck sync and a quiet publisher looked the same.

## Actions (done)

| Action | Where | Proof |
|---|---|---|
| A run stopped by its page budget keeps its cursor; the next continues from it | `ingest/connectors/socrata.py` | `test_a_run_stopped_by_its_page_budget_continues_where_it_stopped_not_from_the_top` |
| A block under one update time larger than 100,000 rows is counted, recorded as a skipped reload and passed | `ingest/connectors/socrata.py` | `test_a_publisher_reload_is_counted_skipped_and_the_real_changes_after_it_land_in_the_same_run` |
| Lag is the publisher's newest update minus the newest landed, asked every run; status "behind" and an alert when it passes the promise while runs succeed | `open_data_daily.py`, `freshness.py`, `alerts.py` | `test_runs_that_succeed_while_the_data_falls_behind_the_source_are_behind_not_on_time` |
| A daily reconcile re-asks NYC for a slice of the rows held, so changes hidden in a skipped reload are found | `ingest/reconcile.py` | `test_reconcile_finds_rows_the_source_changed_without_moving_their_time_and_rows_it_removed` |

## Known limit

The reconcile checks rows we already hold. Rows inside the reload that the feed never held (permits last
changed before the feed began) are out of its scope by design.
