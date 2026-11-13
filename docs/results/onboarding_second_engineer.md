# Onboarding tests: other engineers add sources with the kit

**Question:** can an engineer who did not build the kit add a new source from [the guide](../add_a_source.md)
alone, and how long does it take? This is the measure for "time to onboard a source".

| Engineer | Source | Time to 4 PASS | Guide as given | Found |
|---|---|---|---|---|
| Second | Open-Meteo weather | about 14 min | commands broken (workaround) | 8 things, 2 bugs |
| Third | NYC 311 | 36 min | fixed guide, no workaround | 7 things, 2 bugs (one in shared code) |

**Result (2026-11-13):** a second engineer added Open-Meteo hourly weather, a public API with no key, against
the real API on their own Mac (Python 3.12), in **about 14 minutes** from opening the guide to four PASS lines.
They used Claude for reading, writing the source and reviewing the draft, and ran every command themselves.
So this is the time for an engineer working with an AI assistant, not by hand.

| | |
|---|---|
| Source file | [`examples/sources/open_meteo_weather.py`](../../examples/sources/open_meteo_weather.py), 38 lines |
| Contract | [`sources/kit/open_meteo_weather.yaml`](../../sources/kit/open_meteo_weather.yaml), version 1, reviewed from the draft |
| First run | 120 rows in 5 pages (5 days), 0 quarantined, 2.8 s |
| Second run | asked only for the next day: 24 rows, 1 page, 0.6 s |
| verify | 4 of 4 PASS |
| In CI since | [`tests/test_kit_open_meteo.py`](../../tests/test_kit_open_meteo.py), against a local stand-in |

## What it found, and what changed

The point of the test was to find where the kit and guide fail someone new. It found eight things; all are
fixed in the same change.

| # | Found | Kind | Fix | Proof |
|---|---|---|---|---|
| a | **The guide's commands did not work at all**, even for the worked example: `python -m plumbline.kit` printed "expected one Source subclass, found 0". Under `-m` the file runs as `__main__`, a second copy of the module, so the source's `Source` never matched. The unit tests called the functions directly and never ran the command the guide gives | bug | the `__main__` block runs the real module's `main` | `test_the_guides_commands_work_as_typed_through_python_dash_m` runs the CLI as a subprocess; it failed before the fix |
| b | No word on an API with no "last changed" field | guide | a column for "if the API has none" in step 1 | |
| c | Only offset paging shown; Open-Meteo has no paging | guide | "page by a time window"; this source is now the second worked example | |
| d | verify reads 3 pages, which the guide did not say; a source that rightly holds its cursor to the end of a longer window failed the cursor check | bug | that check says SKIP, not FAIL, when the page limit cut the run; a window that ends exactly at the limit counts as finished | `test_a_window_longer_than_the_page_limit_skips_...`, `test_a_run_whose_window_ends_exactly_at_the_page_limit_is_finished` |
| e | What to return when nothing is new | guide | one line: `Page([], cursor=cursor)` | |
| f | The draft typed a number as text, made a label an enum, and left the id's parts optional | guide | three more items on the draft checklist | |
| g | verify's words contradicted its numbers ("0 already held" after re-reading 72 rows; "ends where a clean run does" at 96 vs 72) | wording | the detail says what is checked: rows read again and added; "loses nothing", at least as many rows | |
| h | `python -m venv` is `python3` on macOS | guide | `python3` | |

**What this says about the kit, plainly:** the design held (the source is 38 lines, and the four checks
passed on a real API with no paging, no id and no "last changed" field), but the shipped command was broken
and only an outside user could have found it, because every test bypassed it. The CLI is now tested the way
the guide uses it.

Known limit of this source, recorded by its author: with no "last changed" field, a day the archive revises
after it was read is not picked up.

## Third engineer: NYC 311, on the fixed guide (2026-11-13)

The point: do the fixes above hold for someone else, on a harder API? **They held.** `python -m plumbline.kit`
worked as typed with no workaround, SKIP behaved as the guide says, and the draft checklist caught every draft
problem but one. Four PASS lines on the real API in **36 minutes**, again with Claude, across four versions of
the source. The machine the final run was on was not stated in the report.

| | |
|---|---|
| Source file | [`examples/sources/nyc_311.py`](../../examples/sources/nyc_311.py), 61 lines: keyset paging, a check for the stale copy |
| Contract | [`sources/kit/nyc_311.yaml`](../../sources/kit/nyc_311.yaml), version 1; addresses, coordinates and free-text notes left out |
| First run | 390 rows, 0 quarantined, 9.2 s; the second asked only for changes and found none |
| verify | 4 of 4 PASS (versions 1 and 3 failed: the stale copy; version 2 SKIPped: a 540,000-row window) |
| In CI since | [`tests/test_kit_nyc_311.py`](../../tests/test_kit_nyc_311.py), a stand-in that also answers from a copy that is behind |

What it found, and what changed:

| # | Found | Kind | Fix | Proof |
|---|---|---|---|---|
| b | **Socrata answers from two copies, one a day behind**, and says so only in a header (`X-SODA2-Data-Out-Of-Date`). Its empty page looked like the end, and `get_json` gave a source no way to see the header | kit gap | `stale_when` on a source: a marked answer is asked again, like a 503, and gives up loudly; `self.last_headers` for the rest | three tests in `tests/test_kit.py`; on the old kit the run landed **0 of 1,200** rows |
| new | Socrata's times end in `Z`; **Python 3.10 cannot read a trailing Z**, so on 3.10 every 311 row was quarantined while 3.12 loaded them. Surfaced when her source went into CI, which runs 3.10 | bug | timestamps parse as 3.11+ reads them, on every version, in the contract loader every source shares | `tests/test_timestamps.py`; the 311 test loaded 0 of 7,000 on 3.10 before |
| c | A "last changed" value thousands of rows share; offset paging skips or repeats them | guide | keyset paging in step 1's table; this source is the third worked example | |
| d | One reload stamps ~540,000 rows; "raise --max-pages" would mean ~2.7 million rows across verify's runs | guide | scope by another field, such as created date | |
| e | The draft marked `incident_zip` as a person's zip (the key for deleting someone's rows); nothing pointed it out | kit + guide | the draft names its identity columns at the top; the checklist says keep one only if it describes a person | `test_the_draft_names_its_identity_columns_...` |
| f | "Nothing new? No request" cannot apply when only a request can tell | guide | both cases spelled out | |
| g | The stand-in advice made for stand-ins that only answer correctly | guide | make the stand-in misbehave like the real API: stale, empty, nulls, 429s | the 311 stand-in serves a stale copy |

**Plainly:** both tests were done with an AI assistant, so the times measure an engineer working with one.
The third test found a gap the first two could not have: an API whose wrong answers look right. Two of the
eight findings were bugs in shared code (the 3.10 timestamp parse affects any source sending `Z` times).
