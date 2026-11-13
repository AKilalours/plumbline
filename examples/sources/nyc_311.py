"""NYC 311 service requests from NYC Open Data (Socrata). A public API with no key:
https://dev.socrata.com/foundry/data.cityofnewyork.us/erm2-nwe9  ($select, $where, $order, $limit)

"Last changed" is Socrata's :updated_at. NYC reloads 311 in bulk, so thousands of rows share one :updated_at
(to the millisecond), and offset paging skips rows that move while it reads. So pages are read by keyset:
ordered by (:updated_at, unique_key), each request asks for the rows after the last one seen. Pages arrive
in cursor order, so every page's cursor is safe to start from.
The API answers from more than one copy, and one can be a day behind: it returns a short or empty page that
looks like the end. So a short page counts as the end only when the newest :updated_at agrees with it.
One reload stamps about half a million rows, so the source reads requests opened since CREATED_SINCE.
Left out as personal or identifying: addresses, streets, intersections, landmark, bbl, coordinates and the
free-text resolution notes.
"""
import time

from plumbline.kit import Page, Source, SourceError

URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
CREATED_SINCE = "2026-11-12T00:00:00"        # requests opened since then, and every later change to them
START = "2026-11-13T00:00:00.000Z|"          # "<updated_at>|<unique_key>": the first run reads from here
FIELDS = ["unique_key", "created_date", "closed_date", "agency", "complaint_type", "descriptor", "status",
          "location_type", "incident_zip", "borough", "community_board", "council_district", "police_precinct",
          "open_data_channel_type", "resolution_action_updated_date"]


class ServiceRequests(Source):
    name, key, updated, url = "nyc_311", "unique_key", "updated_at", URL

    def pages(self, cursor, size=5000):
        at, after = (cursor or START).split("|")
        while True:
            rows, newest, tries = self.read(at, after, size), "", 0
            while len(rows) < size:           # a short page may be the end, or an answer from the copy that is behind
                end = rows[-1]["updated_at"] if rows else at
                if newest == end:
                    break                     # the newest row agrees: this really is the end
                tries += 1
                if tries == 20:
                    raise SourceError(f"{self.name}: the API's copies still disagree after {tries} tries; run again later")
                if tries > 1:
                    time.sleep(self.base_delay)
                if newest < end:              # no answer from the current copy yet (the one behind answers older)
                    newest = max(newest, self.newest() or "")   # null: the copy behind has none yet
                else:                         # newer rows exist, so the page came from the copy that is behind
                    rows = self.read(at, after, size)
            if rows:
                at, after = rows[-1]["updated_at"], rows[-1]["unique_key"]
            yield Page(rows, cursor=f"{at}|{after}")       # rows come in cursor order: safe to start after any page
            if len(rows) < size:
                return

    def read(self, at, after, size):
        where = f":updated_at > '{at}'" + (f" OR (:updated_at = '{at}' AND unique_key > '{after}')" if after else "")
        where = f"created_date >= '{CREATED_SINCE}' AND ({where})"
        body = self.get_json(self.url, **{"$select": ",".join(FIELDS + [":updated_at"]), "$where": where,
                                          "$order": ":updated_at, unique_key", "$limit": size})
        return [{**{f: r.get(f) for f in FIELDS}, "updated_at": r[":updated_at"]} for r in body]

    def newest(self):
        return self.get_json(self.url, **{"$select": "max(:updated_at) as newest",     # same rows the pages read
                                          "$where": f"created_date >= '{CREATED_SINCE}'"})[0].get("newest")
