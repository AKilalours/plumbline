"""Bronze on Iceberg over an S3 API: publishing is a sync, history is kept until a deletion, and a
deleted person leaves no trace in the bucket, not in old snapshots and not in manifest statistics."""
from __future__ import annotations

import csv
import shutil
import socket
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("pyiceberg")
moto_server = pytest.importorskip("moto.server")

from plumbline.ingest.contracts import email_hash, load_contracts  # noqa: E402
from plumbline.ingest.engine import ingest  # noqa: E402
from plumbline.lake import iceberg as L  # noqa: E402
from plumbline.synth.config import SynthConfig  # noqa: E402
from plumbline.synth.generate import generate  # noqa: E402
from plumbline.synth.reference import load_reference  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")
TABLES = ("crm", "vendor", "email")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3():
    """A local S3-compatible server: the same API as AWS S3 or MinIO, no account needed."""
    port = _free_port()
    srv = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    srv.start()
    import boto3
    creds = {"key": "testing", "secret": "testing", "region": "us-east-1", "endpoint": f"http://127.0.0.1:{port}"}
    boto3.client("s3", endpoint_url=creds["endpoint"], aws_access_key_id="testing", aws_secret_access_key="testing",
                 region_name="us-east-1").create_bucket(Bucket="lake")
    yield creds
    srv.stop()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("lake")
    run = d / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=5, n_people=1500), run)
    return run


@pytest.fixture
def setup(world, s3, tmp_path):
    run = tmp_path / "run"
    shutil.copytree(world, run)
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    lake = L.Lake(f"s3://lake/{tmp_path.name}", tmp_path / "catalog.db", s3)
    return run, wh, lake


def _bronze_count(wh, t):
    with duckdb.connect(str(wh), read_only=True) as con:
        return con.execute(f"SELECT count(*) FROM bronze_{t}").fetchone()[0]


def test_publish_matches_bronze_and_a_rerun_commits_nothing(setup):
    run, wh, lake = setup
    out = L.publish(wh, lake, TABLES)
    assert all(out[t]["rows"] == _bronze_count(wh, t) for t in TABLES)
    snaps = {t: len(lake.catalog().load_table(f"bronze.{t}").snapshots()) for t in TABLES}
    again = L.publish(wh, lake, TABLES)
    assert all(again[t]["appended"] == 0 and again[t]["deleted"] == 0 for t in TABLES)
    assert {t: len(lake.catalog().load_table(f"bronze.{t}").snapshots()) for t in TABLES} == snaps


def test_a_new_delivery_is_a_new_snapshot_and_the_old_one_can_still_be_read(setup):
    run, wh, lake = setup
    with duckdb.connect(str(wh)) as con:      # hold back the newest CRM delivery, as if it had not arrived
        last = con.execute("SELECT max(_first_delivery_id) FROM bronze_crm").fetchone()[0]
        held = con.execute("SELECT count(*) FROM bronze_crm WHERE _first_delivery_id = ?", [last]).fetchone()[0]
        con.execute("CREATE TABLE held AS SELECT * FROM bronze_crm WHERE _first_delivery_id = ?", [last])
        con.execute("DELETE FROM bronze_crm WHERE _first_delivery_id = ?", [last])
    L.publish(wh, lake, ("crm",))
    first = lake.catalog().load_table("bronze.crm").current_snapshot().snapshot_id
    with duckdb.connect(str(wh)) as con:
        con.execute("INSERT INTO bronze_crm SELECT * FROM held")
    out = L.publish(wh, lake, ("crm",))
    assert held > 0 and out["crm"]["appended"] == held
    assert L.read(lake, "crm", snapshot_id=first).num_rows == out["crm"]["rows"] - held   # time travel


def _request_deletion(run: Path, email: str) -> None:
    p = run / "sources" / "privacy" / "deletion_requests_2026-01-15.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["request_id", "email_sha256", "phone_sha256", "received_date"])
        w.writerow(["R9000001", email_hash(email), "", "2026-01-15"])


def test_a_deleted_person_leaves_no_trace_in_the_bucket(setup):
    run, wh, lake = setup
    with duckdb.connect(str(wh), read_only=True) as con:   # the alphabetically first address: a likely "lower bound"
        email = con.execute("SELECT min(lower(email)) FROM bronze_crm WHERE email IS NOT NULL").fetchone()[0]
    L.publish(wh, lake, TABLES)
    vals, _ = L.stored_values(lake)
    assert email in vals                                     # it is there before the request
    _request_deletion(run, email)
    ingest(run, wh, CONTRACTS)                               # the warehouse purges it
    out = L.publish(wh, lake, TABLES)
    assert sum(out[t]["deleted"] for t in TABLES) >= 1 and out["history"]["snapshots_expired"] >= 1
    vals, raw = L.stored_values(lake)
    assert email not in vals
    assert not [v for v in vals if len(v) >= 6 and email.startswith(v)], "a truncated bound kept a prefix"
    assert email[:12].encode() not in raw
    for t in TABLES:                                          # and what is left is still exactly bronze
        assert L.read(lake, t).num_rows == _bronze_count(wh, t)
        assert len(lake.catalog().load_table(f"bronze.{t}").snapshots()) == 1


def test_tables_never_store_column_values_in_their_statistics(setup):
    run, wh, lake = setup
    L.publish(wh, lake, ("crm",))
    props = lake.catalog().load_table("bronze.crm").properties
    assert props.get("write.metadata.metrics.default") == "counts"
