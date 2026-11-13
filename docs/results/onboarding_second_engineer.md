# Onboarding test: a second engineer adds a source with the kit

**Question:** can an engineer who did not build the kit add a new source from [the guide](../add_a_source.md)
alone, and how long does it take? This is the measure for "time to onboard a source".

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

A second engineer test with a different API is pending.
