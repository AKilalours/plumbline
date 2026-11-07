"""A Kafka topic as a source: consume in batches, land each batch, then commit its offsets.

Kafka keeps every message for a while and remembers, per consumer group, how far each partition has been read
(the committed offset). What makes this consumer safe is the order of two steps:

1. write the batch as a CSV delivery (atomically), and only then
2. commit the offsets of the messages in it.

A crash between the two means Kafka hands the same messages out again on restart (at least once). They land
again in a batch with the same events, and the event's own id is the contract key, so the warehouse keeps one
copy: effectively once, the same rule the SSE connector follows with its checkpoint. Auto-commit is off;
committing before writing would lose a batch on a crash, which is the mistake this order exists to prevent.

A message that is not valid JSON is not dropped and does not stop the topic: it goes to a dead-letter file with
its partition and offset, and its offset is committed with the batch. Who made a change and free text never
reach the batch (the same transform as the stream).

The consumer is anything with Kafka's poll/commit shape: confluent_kafka.Consumer in production and CI (against
a real broker, tests/test_kafka.py), or a stand-in in the unit tests.
"""
from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .store_api import _atomic_write


@dataclass
class KafkaResult:
    messages: int = 0
    rows: int = 0
    dead_letters: int = 0
    filtered: int = 0
    batches: int = 0
    commits: int = 0
    by_partition: dict = field(default_factory=dict)


def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def consume(consumer, landing_dir: str | Path, *, name: str, transform: Callable[[dict], dict | None],
            max_messages: int | None = None, idle_seconds: float = 5.0, batch_size: int = 500, poll_timeout: float = 1.0,
            after_write: Callable[[int], None] | None = None, clock: Callable[[], float] = time.monotonic) -> KafkaResult:
    """Read until max_messages or until the topic has been quiet for idle_seconds, landing batches in landing_dir."""
    landing = Path(landing_dir)
    landing.mkdir(parents=True, exist_ok=True)
    state_path = landing / "_kafka_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"name": name, "next_batch": 1, "columns": None}
    res = KafkaResult()
    batch, msgs, dead = [], [], []
    quiet_since = clock()

    def flush():
        nonlocal batch, msgs, dead
        if not msgs:
            return
        if batch:
            if state["columns"] is None:
                state["columns"] = list(batch[0])
            _atomic_write(landing / f"{name}_k{state['next_batch']:06d}.csv", _csv(batch, state["columns"]))
        if dead:
            with open(landing / "_dead_letters.jsonl", "a") as f:
                for d in dead:
                    f.write(json.dumps(d) + "\n")
        state["next_batch"] += 1
        _atomic_write(state_path, json.dumps(state, indent=1))
        if after_write:
            after_write(state["next_batch"] - 1)      # tests crash here: written, not yet committed
        consumer.commit(asynchronous=False)          # commits the offsets of everything polled so far
        res.commits += 1
        res.batches += 1
        res.rows += len(batch)
        res.dead_letters += len(dead)
        batch, msgs, dead = [], [], []

    while max_messages is None or res.messages < max_messages:
        m = consumer.poll(poll_timeout)
        if m is None:
            if msgs and len(msgs) > 0:
                flush()
            if clock() - quiet_since >= idle_seconds:
                break
            continue
        if m.error():
            raise RuntimeError(f"Kafka error: {m.error()}")
        quiet_since = clock()
        res.messages += 1
        res.by_partition[m.partition()] = res.by_partition.get(m.partition(), 0) + 1
        msgs.append(m)
        try:
            doc = json.loads(m.value())
            row = transform(doc) if isinstance(doc, dict) else None
            if row is None:
                res.filtered += 1
            else:
                batch.append(row)
        except (ValueError, TypeError):
            dead.append({"topic": m.topic(), "partition": m.partition(), "offset": m.offset(),
                         "value": (m.value() or b"")[:500].decode("utf-8", "replace")})
        if len(msgs) >= batch_size:
            flush()
    flush()
    return res
