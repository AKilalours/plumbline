"""The Kafka source: batches land before their offsets are committed, so a crash between the two redelivers
rather than loses, and the warehouse keeps one copy. The unit tests use a stand-in broker with Kafka's
partition-and-committed-offset behaviour; the last test runs the same consumer against a real Kafka broker
when KAFKA_BOOTSTRAP is set (the `kafka` job in .github/workflows/tests.yml starts one)."""
from __future__ import annotations

import csv
import json
import os
import uuid
from pathlib import Path

import duckdb
import pytest

from plumbline.ingest.connectors.kafka_source import consume
from plumbline.ingest.connectors.sse import recentchange_row
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.store.sse_server import make_events

ROOT = Path(__file__).resolve().parents[1]


class Msg:
    def __init__(self, topic, partition, offset, value):
        self._t, self._p, self._o, self._v = topic, partition, offset, value

    def error(self): return None
    def topic(self): return self._t
    def partition(self): return self._p
    def offset(self): return self._o
    def value(self): return self._v


class Broker:
    """Partitions of messages, and per consumer group the committed offset of each partition."""

    def __init__(self, values: list[bytes], partitions: int = 3, topic: str = "recentchange"):
        self.topic, self.parts = topic, [[] for _ in range(partitions)]
        for i, v in enumerate(values):
            p = self.parts[i % partitions]
            p.append(Msg(topic, i % partitions, len(p), v))
        self.committed: dict[str, list[int]] = {}

    def consumer(self, group: str):
        return Consumer(self, group)


class Consumer:
    def __init__(self, broker: Broker, group: str):
        self.b, self.g = broker, group
        self.pos = list(broker.committed.setdefault(group, [0] * len(broker.parts)))   # a restart resumes here
        self.rr = 0

    def poll(self, timeout):
        for _ in range(len(self.b.parts)):
            p = self.rr % len(self.b.parts)
            self.rr += 1
            if self.pos[p] < len(self.b.parts[p]):
                m = self.b.parts[p][self.pos[p]]
                self.pos[p] += 1
                return m
        return None

    def commit(self, asynchronous=False):
        self.b.committed[self.g] = list(self.pos)


def _values(n, bad_at=None):
    ev = make_events(n, canary_every=0)
    vals = [json.dumps(e).encode() for e in ev]
    if bad_at is not None:
        vals[bad_at] = b"{not json"
    return ev, vals


def _landed(folder: Path) -> list[dict]:
    return [r for p in sorted(folder.glob("*.csv")) for r in csv.DictReader(open(p, encoding="utf-8"))]


def _load(run: Path, wh: Path) -> int:
    cdir = wh.parent / "contracts"
    cdir.mkdir(exist_ok=True)
    (cdir / "w.yaml").write_text((ROOT / "sources/streams/wikimedia_recentchange.yaml").read_text())
    ingest(run, wh, load_contracts(cdir))
    with duckdb.connect(str(wh), read_only=True) as con:
        n, d = con.execute("SELECT count(*), count(DISTINCT event_id) FROM current_wikimedia_recentchange").fetchone()
    assert n == d
    return n


def test_every_message_lands_once_and_offsets_are_committed_per_batch(tmp_path):
    ev, vals = _values(1000)
    b = Broker(vals)
    r = consume(b.consumer("plumbline"), tmp_path, name="w", transform=recentchange_row, batch_size=200, idle_seconds=0)
    assert r.messages == r.rows == 1000 and r.batches == r.commits == 5
    assert sum(b.committed["plumbline"]) == 1000 and sorted(r.by_partition.values()) == [333, 333, 334]
    assert {x["event_id"] for x in _landed(tmp_path)} == {e["meta"]["id"] for e in ev}


def test_a_crash_after_writing_before_committing_redelivers_and_the_warehouse_keeps_one_copy(tmp_path):
    ev, vals = _values(1000)
    b = Broker(vals)
    landing = tmp_path / "run" / "sources" / "wikimedia_recentchange"

    def crash(batch):
        if batch == 3:
            raise RuntimeError("killed after batch 3 was written, before its offsets were committed")
    with pytest.raises(RuntimeError, match="killed"):
        consume(b.consumer("g"), landing, name="wikimedia_recentchange", transform=recentchange_row, batch_size=200,
                idle_seconds=0, after_write=crash)
    assert sum(b.committed["g"]) == 400                       # batches 1 and 2 were committed, batch 3 was not
    r = consume(b.consumer("g"), landing, name="wikimedia_recentchange", transform=recentchange_row, batch_size=200, idle_seconds=0)
    assert r.messages == 600                                  # Kafka handed batch 3 out again
    assert len(_landed(landing)) == 1200                      # so it is on disk twice...
    assert _load(tmp_path / "run", tmp_path / "w.duckdb") == 1000      # ...and once in the warehouse


def test_a_message_that_is_not_json_goes_to_dead_letters_and_the_topic_keeps_moving(tmp_path):
    ev, vals = _values(300, bad_at=17)
    b = Broker(vals)
    r = consume(b.consumer("g"), tmp_path, name="w", transform=recentchange_row, batch_size=100, idle_seconds=0)
    assert r.messages == 300 and r.rows == 299 and r.dead_letters == 1 and sum(b.committed["g"]) == 300
    (d,) = [json.loads(x) for x in (tmp_path / "_dead_letters.jsonl").read_text().splitlines()]
    assert d["value"] == "{not json" and (d["partition"], d["offset"]) == (17 % 3, 17 // 3)


def test_who_made_a_change_never_reaches_a_batch(tmp_path):
    ev, vals = _values(200)
    consume(Broker(vals).consumer("g"), tmp_path, name="w", transform=recentchange_row, idle_seconds=0)
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.csv"))
    assert "Editor" not in text and "free text" not in text


@pytest.mark.skipif(not os.environ.get("KAFKA_BOOTSTRAP"), reason="needs a real Kafka broker (KAFKA_BOOTSTRAP)")
def test_against_a_real_kafka_broker_a_crash_redelivers_and_nothing_is_lost_or_doubled(tmp_path):
    from confluent_kafka import Consumer, Producer
    from confluent_kafka.admin import AdminClient, NewTopic
    boot = os.environ["KAFKA_BOOTSTRAP"]
    topic, group = f"recentchange-{uuid.uuid4().hex[:8]}", f"plumbline-{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": boot})
    admin.create_topics([NewTopic(topic, num_partitions=3, replication_factor=1)])[topic].result(30)
    ev, vals = _values(2000, bad_at=5)
    p = Producer({"bootstrap.servers": boot})
    for i, v in enumerate(vals):
        p.produce(topic, key=str(i).encode(), value=v)
    assert p.flush(30) == 0

    def consumer():
        c = Consumer({"bootstrap.servers": boot, "group.id": group, "enable.auto.commit": False, "auto.offset.reset": "earliest"})
        c.subscribe([topic])
        return c
    landing = tmp_path / "run" / "sources" / "wikimedia_recentchange"

    def crash(batch):
        if batch == 2:
            raise RuntimeError("killed after batch 2 was written, before its offsets were committed")
    c1 = consumer()
    with pytest.raises(RuntimeError, match="killed"):
        consume(c1, landing, name="wikimedia_recentchange", transform=recentchange_row, batch_size=300, idle_seconds=10,
                after_write=crash)
    c1.close()                                                # leaves the group without committing batch 2
    c2 = consumer()
    r = consume(c2, landing, name="wikimedia_recentchange", transform=recentchange_row, batch_size=300, idle_seconds=10)
    committed = c2.committed(c2.assignment(), timeout=10)
    c2.close()
    dead = {(d["partition"], d["offset"]) for d in map(json.loads, (landing / "_dead_letters.jsonl").read_text().splitlines())}
    assert len(dead) == 1 and len(r.by_partition) >= 1                  # the bad message, kept once per delivery
    assert sum(t.offset for t in committed) == 2000                     # every message committed, once consumed
    assert len(_landed(landing)) > 1999                                  # batch 2 landed twice
    assert _load(tmp_path / "run", tmp_path / "w.duckdb") == 1999        # one copy each; the bad message dead-lettered
