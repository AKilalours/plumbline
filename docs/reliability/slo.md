# Service level objectives

What the two real feeds promise the people who use their data, how each promise is measured, and what happens
when one is broken. Measured by `python -m plumbline.ingest.slo` from the run lines the jobs write (never
entered by hand); shown on the app's Sources → Health page.

## The promises

| Promise | Good event | Target | Why this target |
|---|---|---|---|
| Runs succeed | a run that finished and, where a cloud warehouse is set, published | 95% of runs | a scheduled job on shared CI runners fails now and then for reasons outside the code; 1 in 20 is the most a consumer should see |
| Data is fresh | an hour in which a run succeeded within the last 26 h and, where measured, the data it landed was within 26 h of the publisher's newest update | 99% of hours | the consumers plan by the day; 26 h is a day plus slack for a late runner |
| What we hold matches the source | a row the reconcile checked that the source still has, with the same values read the way the loader reads them | 99.5% of rows checked | a wrong row costs a consumer a wrong decision; 1 in 200 is the most this project accepts |

Window: 28 days, rolling; a younger feed is measured since its first run, and the page says how many days that is.

## The error budget

The budget is what a target allows to go wrong: (1 − target) × total. *Burn* is the share of the budget spent
divided by the share of the window gone; above 1 the budget runs out before the window ends.

**When a feed's budget is spent:** no new features on that feed's code until the postmortem of what spent it is
written and its actions are done; changes that reduce the risk (pins, tests, rollbacks) go first. When a
promise has no measurement yet (the stream has no reconcile; freshness against the source exists from
2026-11-12), it is shown as "not yet" and never as met.

## Where the feeds stand (measured 2026-11-12 15:00 UTC)

| Feed | Runs succeed | Data is fresh | Matches the source |
|---|---|---|---|
| NYC Open Data permits (5.5 days) | **92.3%** (24 of 26): budget spent | 100% of 131 hours | 99.5% of 2,000 rows checked: on target, budget used up |
| Wikimedia live stream (5.4 days) | **92.0%** (23 of 25): budget spent | 100% of 129 hours | not measured (no reconcile for a stream) |

Both run budgets were spent on 2026-11-07, the first day, by one incident: the cloud warehouse refused a newer
DuckDB and failed two runs of each feed ([postmortem](../postmortems/2026-11-07-motherduck-duckdb.md)). Its
actions are done, and every run since has succeeded. The first reconcile slice found 4 rows with a missing
apartment number (a connector bug, [postmortem](../postmortems/2026-11-07-apartment-number-dropped.md)) and 6 rows
NYC no longer has.

Read with care: 26 runs is a small sample, where one failure moves the number almost 4 points. And "fresh" was
met through the NYC reload stall ([postmortem](../postmortems/2026-11-10-nyc-reload-stall.md)) because the data
was at most 24.1 hours behind, inside the 26-hour promise: a promise that tight enough to catch that stall would
also page on every quiet weekend at the city, which is why the stall is now caught by a different check
(data behind the source while runs pass) rather than by tightening this one.
