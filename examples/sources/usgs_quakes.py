"""Earthquakes from the USGS public feed: the worked example in docs/add_a_source.md.

USGS asks for no key and documents this API for programs to use:
https://earthquake.usgs.gov/fdsnws/event/1/  (updatedafter, offset, limit)
"""
from datetime import datetime, timezone

from plumbline.kit import Page, Source

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


class Quakes(Source):
    name, key, updated, url = "usgs_quakes", "event_id", "updated_at", URL

    def pages(self, cursor, size=500):
        since, newest, offset = cursor or "2026-11-01T00:00:00", None, 1
        while True:
            body = self.get_json(self.url, format="geojson", updatedafter=since, orderby="time-asc", limit=size, offset=offset)
            rows = [{"event_id": f["id"], "updated_at": iso(f["properties"]["updated"]), "magnitude": f["properties"]["mag"],
                     "place": f["properties"]["place"], "happened_at": iso(f["properties"]["time"]),
                     "status": f["properties"]["status"]} for f in body["features"]]
            newest = max([newest or since] + [r["updated_at"] for r in rows])
            last = len(rows) < size
            yield Page(rows, cursor=newest if last else None)   # a cursor only once the whole window is read
            if last:
                return
            offset += size


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
