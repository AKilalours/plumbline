# Add a source

You write how to ask the source for the next page. The kit does the rest: retries, safe reruns, crash
recovery, the contract check, privacy, loading, and one report line per run. The worked example is
[`examples/sources/usgs_quakes.py`](../examples/sources/usgs_quakes.py) (USGS earthquakes, a public API with
no key): 32 lines with the docstring, about 15 of them the method you write.

Expect about 30 to 60 minutes for your first source. If it takes longer, the guide is wrong somewhere:
say where.

## 0. Set up (once)

```bash
git clone https://github.com/AKilalours/plumbline && cd plumbline
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/test_kit.py -q          # all passed, none failed: the kit works on your machine
```

## 1. Pick a source

Any API that returns pages of JSON. Good first choices: a public API whose terms allow programs (most
government open data does), no personal data, and a field that says when a record last changed.

Find three things in its docs before writing code:

| You need | USGS example | If the API has none |
|---|---|---|
| The id of a record | `id` | Build a stable one from the fields that identify a record (weather: place + hour) |
| When a record last changed, and how to ask for "changed after X" | `properties.updated`, `updatedafter=` | Leave `updated = None`; the draft leaves `latest_by` empty, and each row is read once. A later correction by the source is not picked up: say so in your contract |
| How to get the next page | `offset=` and `limit=` | Page by a time window instead: one request per day, the cursor is the last day read |
| A "last changed" value many rows share (a bulk reload stamps thousands to the millisecond) | (USGS: rare) | Page by **keyset**: order by (last changed, unique id) and ask for the rows after the last one seen. Offset paging skips or repeats rows that move while it reads. Check the id can be compared with `>` first: Socrata's `:id` cannot |
| A first window that is huge (one reload can stamp half a million rows) | | Scope the source by another field too, such as created date, rather than raising `--max-pages`: `verify` runs the source five times |

Three worked examples: [`usgs_quakes.py`](../examples/sources/usgs_quakes.py) (paging by offset, a real
"last changed" field), [`open_meteo_weather.py`](../examples/sources/open_meteo_weather.py) (no paging,
no "last changed", no id; written by the second engineer to try this guide) and
[`nyc_311.py`](../examples/sources/nyc_311.py) (keyset paging through a bulk reload, an API that answers
from a copy that is behind; written by the third).

## 2. Write the source

Copy the example to `examples/sources/<your_source>.py` and change it. The whole contract between you and
the kit is one method:

```python
class Quakes(Source):
    name, key, updated, url = "usgs_quakes", "event_id", "updated_at", URL

    def pages(self, cursor, size=500):
        since, newest, offset = cursor or "2026-11-01T00:00:00", None, 1
        while True:
            body = self.get_json(self.url, format="geojson", updatedafter=since,
                                 orderby="time-asc", limit=size, offset=offset)
            rows = [{"event_id": f["id"], "updated_at": iso(f["properties"]["updated"]), ...}
                    for f in body["features"]]
            newest = max([newest or since] + [r["updated_at"] for r in rows])
            last = len(rows) < size
            yield Page(rows, cursor=newest if last else None)
            if last:
                return
            offset += size
```

- `cursor` is whatever you gave the kit at the end of the last run (`None` the first time).
- Yield one `Page` per request. Rows are plain dicts; nested values are stored as JSON text.
- Nothing new yet? If you can tell without asking (a date window not reached yet), yield
  `Page([], cursor=cursor)` and return. If only a request can tell you, an empty page from it is fine too.
- **An empty or short page may not be the end.** APIs behind a load balancer can answer from a copy that is
  behind (Socrata does, and says so with `X-SODA2-Data-Out-Of-Date: true`). Set
  `stale_when = {"X-SODA2-Data-Out-Of-Date": "true"}` on the class and the kit asks again, like a 503;
  `self.last_headers` holds the last answer's headers if you need to judge it yourself.
- Use `self.get_json(url, **params)`, not `requests` or `urllib`: it is where the retries live.
- A token goes in `headers` from an environment variable, never in the file:
  `headers = {"Authorization": f"Bearer {os.environ['MY_API_TOKEN']}"}`. The kit never prints headers.

### The one rule that matters: give a cursor only when it is safe to start from

USGS returns events in **time** order, but the cursor is the **update** time. A quake from page 1 can be
revised after one on page 3. If you save "the newest update seen on page 1", a crash before page 2
means the next run asks for "changed after that", and every page 2 row updated earlier is gone for good.

So the example yields `cursor=None` on every page but the last ("keep the old cursor"). A crash
re-reads the window from the start, and reading a row twice costs nothing: the loader stores each row
once. `verify` (step 5) fails a source that gets this wrong; `tests/test_kit.py` has the careless version.

When pages arrive **in cursor order** (sorted by the update time, or one whole day per page, oldest first),
every page's cursor is safe: give one on every page. That is what the Open-Meteo example does.

## 3. Draft the contract

```bash
python -m plumbline.kit draft examples/sources/<your_source>.py
```

This reads one page and writes `sources/drafts/<name>.yaml`: a type for every column, your `key` and
`updated` as the key and the "newest wins" column, and a note at the top listing any field it left out
as personal. **Read it.** It saw one page, so check:

- `enum` lists: the draft only knows the values it saw. Add the ones the docs list (the USGS status has
  three; the first page had two). A label you set yourself (a place name) is a `string`, not an enum.
- Types the draft guessed wrong: a number column whose first page had a gap can come out as `string`.
  Check every column against the API's docs.
- Numbers that are really ids (zip codes, codes with leading zeros) should be `string`.
- The columns your id is built from are `required: true`, like the id itself.
- Anything personal stays out unless there is a reason and a privacy rule for it.
- `identity:` lines: the draft marks columns like a zip, email or phone as identity, the keys used to find a
  person's rows when they ask to be deleted. Keep one only if it describes a **person**. Where an incident
  happened, or a business's phone, is not a person: remove the line.

Then move it to `sources/kit/<name>.yaml` and add `version: 1`. From now on, a change that would break
readers (a column removed, a type changed) fails CI unless you raise `version`.

## 4. Run it

```bash
python -m plumbline.kit run examples/sources/<your_source>.py --max-pages 5
```

```
usgs_quakes: 1200 rows landed in 3 pages, 1200 new, 0 already held, 0 quarantined, 0 retries, 2.1s
```

Run it again: it asks only for what changed since the cursor. Rows that break the contract are in the
`quarantine` table with the reason, not loaded. A field the contract does not list shows as
`held back <field>`: add it to the contract if you want it.

## 5. Verify it

```bash
python -m plumbline.kit verify examples/sources/<your_source>.py
```

```
PASS  it lands and loads rows
PASS  reading the same pages again adds nothing
PASS  a crash after page 1 resumes and loses nothing
PASS  a finished run leaves a cursor
```

All four must pass before the source ships. `verify` reads at most 3 pages a run (`--max-pages` to change
it). If your window is longer and you rightly hold the cursor until it ends, the cursor check says SKIP,
not FAIL: rerun with a larger `--max-pages` to judge it. Then add a test like `tests/test_kit.py` against a small
stand-in for your API (`src/plumbline/store/usgs_server.py` is the pattern), so CI checks it without the
network. Make the stand-in misbehave the way the real API does, not only answer correctly: stale or empty
answers, nulls where a value should be, 429s. The NYC 311 source passed against a polite stand-in and broke
on the real API's stale copy ([its test](../tests/test_kit_nyc_311.py) now serves one).

## When something goes wrong

| You see | It means | Do |
|---|---|---|
| `SourceError: HTTP 400 ... ` | The source refused the request; a retry will not help | Read the message: usually a parameter name |
| `gave up after 7 tries` | 429 or 5xx every time | The source is down or you are too fast; raise `base_delay` |
| `no contract for <name>` | Step 3 is not done | Draft, read, move to `sources/kit/` |
| `the draft does not load ... Set key` | The draft could not find a unique id | Set `key = "<id column>"` on the class |
| Many rows quarantined | The contract is stricter than the data | `SELECT reason, count(*) FROM quarantine` in `data/kit/kit.duckdb` |
| `verify` crash check FAILs | Your cursor is saved before the window is read | See "the one rule" above |

## For the onboarding test

If you are trying this as the second engineer: pick a **different** source than USGS, follow this page
without asking the author, and send back three things:

1. Minutes from opening this page to four PASS lines.
2. How many lines your source file has.
3. Every place this page was wrong, unclear, or made you read the kit's code.

Those numbers are the measure: time to onboard a source, by someone who did not build the kit.
