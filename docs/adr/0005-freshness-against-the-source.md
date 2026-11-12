# 0005 · Lag is measured against the source's newest update, not the clock

*2026-11-12*

**Context.** "Hours behind" was now minus the newest row landed. A quiet weekend at the city and a stuck sync
looked the same, so the stuck sync of 2026-11-10 to 12 raised nothing while the measure read 29 hours.

**Decision.** Each run asks the source for its newest update time (one aggregate request) and records how far the
newest landed update is behind it. A feed whose runs pass while that gap exceeds its 26-hour promise is "behind",
on the page and as an alert.

**Rejected.** Alerting on "no new rows for N hours": noisy for a source that publishes in bursts. Run success
alone: it is what failed.

**Costs.** One extra request a run; a source must expose its newest update time.

**Evidence.** `test_runs_that_succeed_while_the_data_falls_behind_the_source_are_behind_not_on_time`,
`test_a_feed_whose_data_stops_moving_while_runs_pass_is_named`, `test_lag_is_measured_against_the_publishers_newest_update_not_the_clock`.

**Revisit when** a source cannot say what its newest update is; then compare against a reconcile slice instead.
