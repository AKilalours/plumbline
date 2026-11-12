# Postmortem: an approved column was dropped for five days

**Date:** 2026-11-07 03:45 to 2026-11-12 UTC · **Severity:** a field silently missing · **Status:** resolved;
repair in progress through the reconcile · Blameless.

## Summary

NYC's API leaves a field out of a page when it is empty on every row of that page. The Socrata connector fixed
its column list from the first page it ever read, and treated any column seen later as drift: reported on the
run line, never written. `apt_condo_no_s` (apartment or condo number) was empty on every row of the first page
on 2026-11-07, so from then on every apartment number was dropped, although the reviewed contract lists the
column. Every run passed; each run line said "new columns held back: apt_condo_no_s".

## Impact

- Apartment numbers missing from NYC permit rows landed 2026-11-07 to 12.
- Measured, not guessed: the first reconcile slice (2,000 of the 68,811 rows held, chosen by a hash of the row
  id, so a fair sample) found **4 rows (0.2%)** whose only difference from NYC was the missing apartment
  number. Most permits have none. Across all rows held that suggests on the order of 140; the reconcile will
  have counted every one when its pass completes (about 35 runs).

## Detection

**Manual,** on 2026-11-12, while reviewing the app: the contract lists the column, the run line said it was
held back; both could not be right. The signal had been on every run line for five days, but drift was
reported, never alerted.

## Root cause

1. **The connector decided the shape of the data alone.** It did not know the contract had already approved
   the column, so "seen after the first page" meant "unapproved".
2. **A persistent warning was not an alert.** Information shown on every run line, with no one asked to act.

## Actions (done)

| Action | Where | Proof |
|---|---|---|
| The connector takes the contract's columns as approved; an approved column that first appears later is written | `ingest/connectors/socrata.py`, `open_data_daily.py` | `test_a_column_the_contract_approves_is_written_when_it_first_appears_after_the_first_page` |
| An approved column already on the drift list is promoted at the start of a sync, whether or not that run's pages carry it (the first run after the fix re-read pages where it was empty) | `ingest/connectors/socrata.py` | `test_an_approved_column_already_called_drift_is_promoted_even_when_no_page_carries_it` |
| Rows already landed without it are repaired: the reconcile finds them as changed and reloads NYC's current version | `ingest/reconcile.py` | the 2026-11-12 13:35 run: 4 changed, all `apt_condo_no_s` |
| A new column held back three runs in a row opens an alert asking a person to approve or drop it | `ingest/alerts.py` | `test_a_new_column_held_back_three_runs_running_asks_a_person_to_decide` |
