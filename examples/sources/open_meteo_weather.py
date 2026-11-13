"""Hourly weather from the Open-Meteo historical archive, one location. A public API with no key:
https://open-meteo.com/en/docs/historical-weather-api  (start_date, end_date, hourly=...)

The API has no record id, no "last changed" field and no paging: one request returns every hour of a
date range. So the source pages by day, the id is location + hour, and the cursor is the last day read.
Days arrive in order and each page holds a whole day, so every page's cursor is safe to start from.
"""
from datetime import date, datetime, timedelta, timezone

from plumbline.kit import Page, Source

URL = "https://archive-api.open-meteo.com/v1/archive"
PLACE, LAT, LON = "new_york_ny", 40.7128, -74.0060
FIELDS = {"temperature_2m": "temperature_c", "relative_humidity_2m": "humidity_pct",
          "precipitation": "precipitation_mm", "wind_speed_10m": "wind_speed_kmh", "weather_code": "weather_code"}
LAG_DAYS = 7          # the archive trails today by about five days; stay clear of half-filled days


class Weather(Source):
    name, key, updated, url = "open_meteo_weather", "weather_id", None, URL

    def pages(self, cursor):
        day = date.fromisoformat(cursor) + timedelta(days=1) if cursor else date(2026, 11, 1)
        last = datetime.now(timezone.utc).date() - timedelta(days=LAG_DAYS)
        if day > last:
            yield Page([], cursor=cursor)                 # nothing new yet: ask nothing, keep the cursor
            return
        while day <= last:
            body = self.get_json(self.url, latitude=LAT, longitude=LON, start_date=day.isoformat(),
                                 end_date=day.isoformat(), hourly=",".join(FIELDS), timezone="UTC")
            h = body["hourly"]
            rows = [{"weather_id": f"{PLACE}|{t}", "location": PLACE, "latitude": LAT, "longitude": LON,
                     "observed_at": t + ":00", **{out: h[f][i] for f, out in FIELDS.items()}}
                    for i, t in enumerate(h["time"])]
            if all(r["temperature_c"] is None for r in rows):
                return                                    # not in the archive yet: keep the cursor, retry next run
            yield Page(rows, cursor=day.isoformat())      # a whole day, in order: safe to start after it
            day += timedelta(days=1)
