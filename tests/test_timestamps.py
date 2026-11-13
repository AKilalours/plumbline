"""Timestamps load the same on every Python CI runs. On 3.10, "2026-11-13T02:36:35.285Z" (Socrata's format) was a
bad timestamp and its row quarantined, while 3.12 loaded it: the NYC 311 source loaded 0 of 7,000 rows on 3.10."""
from datetime import datetime, timedelta, timezone

import pytest

from plumbline.ingest.contracts import BadValue, Column, normalize, parse_timestamp

UTC = timezone.utc


@pytest.mark.parametrize("raw,want", [
    ("2026-11-13T02:36:35.285Z", datetime(2026, 11, 13, 2, 36, 35, 285000, tzinfo=UTC)),
    ("2026-11-13T02:36:35z", datetime(2026, 11, 13, 2, 36, 35, tzinfo=UTC)),
    ("2026-11-13T02:36:35.28", datetime(2026, 11, 13, 2, 36, 35, 280000)),
    ("2026-11-13T02:36:35.1+05:30", datetime(2026, 11, 13, 2, 36, 35, 100000, tzinfo=timezone(timedelta(hours=5, minutes=30)))),
    ("2026-11-13T02:36:35", datetime(2026, 11, 13, 2, 36, 35)),
    ("2026-11-13", datetime(2026, 11, 13)),
])
def test_iso_timestamps_parse_as_python_3_11_reads_them(raw, want):
    assert parse_timestamp(raw) == want


def test_a_contract_timestamp_column_takes_a_trailing_z_instead_of_quarantining_the_row():
    col = Column(name="updated_at", type="timestamp", required=True)
    assert normalize(col, "2026-11-13T02:36:35.285Z") == datetime(2026, 11, 13, 2, 36, 35, 285000, tzinfo=UTC)
    with pytest.raises(BadValue, match="bad_timestamp"):
        normalize(col, "13/11/2026")
