"""The loader's "current" rule, run on Spark over Parquet, checked row for row against DuckDB.

    python -m plumbline.ingest.spark_current WAREHOUSE        # export to Parquet, run Spark, compare, save the result

Everything else in this project runs on DuckDB on one machine. The claim that the design moves to a cluster
engine is only worth something if the hard part moves and gives the same answer. The hard part of the loader is
deciding which version of each record is live: only the newest version of each delivered file counts (a
corrected _v2 replaces _v1), a row is live if that newest delivery contains it, a person who asked to be deleted
is erased by the same email-and-phone rule the loader uses, and among what is left the newest version of each
key wins (by the source's own date columns, then by arrival).

This module writes that rule again with Spark's DataFrame operations (joins, an anti-join, a window), not by
reusing the DuckDB SQL, reads the warehouse's tables as Parquet (the shape a data lake holds them in), and
compares the result with DuckDB's current_<source> views: the same keys, each with the same row version.
Spark runs in local mode here; the same code takes a cluster master URL.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TABLES = ("ingest_ledger", "delivery_rows", "suppression")


def export(warehouse: str | Path, lake: Path, contracts) -> None:
    """Every table the rule reads, as Parquet: what a lake would hold."""
    import duckdb
    lake.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        for t in TABLES + tuple(c.table for c in contracts.values() if c.role == "data"):
            con.execute(f"COPY (SELECT * FROM {t}) TO '{(lake / t).as_posix()}.parquet' (FORMAT parquet)")


def current(spark, lake: Path, c, *, erase: bool = True, newest_only: bool = True):
    """The current version of each record of one source, computed by Spark. The two switches exist only so the
    tests can break the rule on purpose and show the comparison notices."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    read = lambda t: spark.read.parquet(str(lake / f"{t}.parquet"))
    ledger = read("ingest_ledger").where((F.col("source") == c.name) & (F.col("status") == "loaded"))
    newest_v = ledger.groupBy("group_key").agg(F.max("version").alias("version"))
    newest = ledger.join(newest_v, ["group_key", "version"]).select("delivery_id") if newest_only else ledger.select("delivery_id")
    live = (read("delivery_rows").where(F.col("source") == c.name).join(newest, "delivery_id")
            .select(F.col("row_hash").alias("_row_hash")).distinct())
    rows = read(c.table).join(live, "_row_hash", "left_semi")

    e = next((n for n, col in c.columns.items() if col.privacy == "email"), None)
    p = next((n for n, col in c.columns.items() if col.privacy == "phone"), None)
    sup = read("suppression")
    erased = []
    if not erase:
        e = p = None
    if p:                                           # a phone match erases
        phones = sup.where(F.col("phone_sha256").isNotNull()).select(F.col("phone_sha256").alias(f"_sha_{p}")).distinct()
        erased.append(rows.join(phones, f"_sha_{p}", "left_semi").select("_row_hash"))
    if e:                                           # an email match erases unless both phones are known and differ
        s = sup.select(F.col("email_sha256").alias(f"_sha_{e}"), F.col("phone_sha256").alias("_req_phone"))
        hit = rows.join(s, f"_sha_{e}")
        if p:
            hit = hit.where(F.col(f"_sha_{p}").isNull() | F.col("_req_phone").isNull() | (F.col("_req_phone") == F.col(f"_sha_{p}")))
        erased.append(hit.select("_row_hash"))
    for x in erased:
        rows = rows.join(x.distinct(), "_row_hash", "left_anti")

    order = [F.col(k).desc_nulls_last() for k in c.latest_by] + [F.col("_first_seen_at").desc(), F.col("_first_version").desc(),
                                                                  F.col("_first_row_number").desc()]
    w = Window.partitionBy(*[F.col(k) for k in c.key]).orderBy(*order)
    return rows.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") == 1).drop("_rn")


def compare(warehouse: str | Path, contracts=None, master: str = "local[2]", **rule) -> dict:
    import duckdb
    from pyspark.sql import SparkSession
    from .contracts import load_contracts
    contracts = contracts or load_contracts(ROOT / "sources")
    out = {"warehouse_rows": {}, "spark_rows": {}, "only_in_duckdb": {}, "only_in_spark": {}, "seconds": {}}
    with tempfile.TemporaryDirectory() as tmp:
        lake = Path(tmp) / "lake"
        t0 = time.perf_counter()
        export(warehouse, lake, contracts)
        out["export_seconds"] = round(time.perf_counter() - t0, 1)
        spark = (SparkSession.builder.master(master).appName("plumbline-current").config("spark.ui.enabled", "false")
                 .config("spark.sql.session.timeZone", "UTC").config("spark.sql.shuffle.partitions", "8").getOrCreate())
        spark.sparkContext.setLogLevel("ERROR")
        out["spark_version"] = spark.version
        try:
            for c in contracts.values():
                if c.role != "data":
                    continue
                t0 = time.perf_counter()
                keys = list(c.key) + ["_row_hash"]
                current(spark, lake, c, **rule).select(*keys).write.parquet(str(Path(tmp) / f"spark_{c.name}"))
                out["seconds"][c.name] = round(time.perf_counter() - t0, 1)
                with duckdb.connect(str(warehouse), read_only=True) as con:
                    cols = ", ".join(f'"{k}"' for k in keys)
                    sp = f"read_parquet('{(Path(tmp) / f'spark_{c.name}').as_posix()}/*.parquet')"
                    out["warehouse_rows"][c.name] = con.execute(f"SELECT count(*) FROM current_{c.name}").fetchone()[0]
                    out["spark_rows"][c.name] = con.execute(f"SELECT count(*) FROM {sp}").fetchone()[0]
                    out["only_in_duckdb"][c.name] = con.execute(
                        f"SELECT count(*) FROM (SELECT {cols} FROM current_{c.name} EXCEPT ALL SELECT {cols} FROM {sp})").fetchone()[0]
                    out["only_in_spark"][c.name] = con.execute(
                        f"SELECT count(*) FROM (SELECT {cols} FROM {sp} EXCEPT ALL SELECT {cols} FROM current_{c.name})").fetchone()[0]
        finally:
            spark.stop()
    out["identical"] = all(v == 0 for d in ("only_in_duckdb", "only_in_spark") for v in out[d].values()) and \
        out["warehouse_rows"] == out["spark_rows"]
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("warehouse")
    ap.add_argument("--save", help="write the comparison to this JSON file")
    a = ap.parse_args(argv)
    r = compare(a.warehouse)
    print(json.dumps(r, indent=1))
    if a.save:
        Path(a.save).write_text(json.dumps(r, indent=1))
    return 0 if r["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
