"""Publish bronze to Iceberg on S3-compatible storage, and make deletion reach the files.

Iceberg keeps every snapshot, so any engine (Spark, Trino, Snowflake, DuckDB) can read a table as it
was last Tuesday. That is the feature, and for personal data it is also the risk: a person deleted
today is still in last Tuesday's snapshot, and in the Parquet files behind it, which nothing ever
rewrites. Deleting the rows from the current table is not deletion.

So publishing is a sync with the warehouse, which is the source of truth:
1. rows the warehouse has and the lake does not are appended (one snapshot per publish; a rerun
   with nothing new commits nothing);
2. rows the lake has and the warehouse no longer has (purged by a deletion request, or removed by
   person-level deletion) are deleted from the current table;
3. if anything was deleted, every older snapshot is expired, and every data file and manifest no
   remaining snapshot references is removed from the bucket. Time travel before that point is gone:
   deletion wins over history, on purpose.

And one setting the defaults get wrong for personal data: Iceberg writes each column's lowest and
highest value into the manifests, cut to 16 characters. A delete commit keeps an entry for the file
it replaced, with those bounds, in the live manifest, where no cleanup may touch it. The first
version of this module left "aadams@kestrelma" in the bucket after deleting aadams@kestrelmail.test.
Tables are therefore created with write.metadata.metrics.default = counts: null and value counts,
no values. Bounds on names and emails barely help pruning anyway.

A test decodes every file in the bucket afterwards (Parquet values, manifest bounds, raw bytes) and
finds no trace of the deleted address, not even a prefix.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from pyarrow import fs as pafs

NAMESPACE = "bronze"
TABLES = ("crm", "vendor", "email", "store")
KEY = "_row_hash"
# counts, not values: see the module note on manifest bounds
TABLE_PROPERTIES = {"write.metadata.metrics.default": "counts", "format-version": "2"}


@dataclass
class Lake:
    warehouse: str                 # s3://bucket/prefix or a local directory
    catalog_db: Path               # the catalog: a SQLite file that records each table's current metadata
    s3: dict | None = None         # endpoint, key, secret, region for S3-compatible storage

    @classmethod
    def from_env(cls, catalog_db="data/lake/catalog.db") -> "Lake":
        """PLUMBLINE_LAKE_URI (s3://... or a path) and, for S3, the usual AWS_* variables plus
        PLUMBLINE_S3_ENDPOINT for a non-AWS store such as MinIO."""
        uri = os.environ.get("PLUMBLINE_LAKE_URI", "data/lake/warehouse")
        s3 = None
        if uri.startswith("s3://"):
            s3 = {"endpoint": os.environ.get("PLUMBLINE_S3_ENDPOINT"), "key": os.environ.get("AWS_ACCESS_KEY_ID"),
                  "secret": os.environ.get("AWS_SECRET_ACCESS_KEY"), "region": os.environ.get("AWS_REGION", "us-east-1")}
        return cls(uri, Path(catalog_db), s3)

    def catalog(self):
        from pyiceberg.catalog.sql import SqlCatalog
        self.catalog_db.parent.mkdir(parents=True, exist_ok=True)
        props = {"uri": f"sqlite:///{self.catalog_db}", "warehouse": self.warehouse}
        if self.s3:
            props.update({k: v for k, v in {"s3.endpoint": self.s3.get("endpoint"), "s3.access-key-id": self.s3.get("key"),
                                            "s3.secret-access-key": self.s3.get("secret"),
                                            "s3.region": self.s3.get("region")}.items() if v})
        else:
            Path(self.warehouse).mkdir(parents=True, exist_ok=True)
            props["warehouse"] = Path(self.warehouse).resolve().as_uri()
        cat = SqlCatalog("plumbline", **props)
        if (NAMESPACE,) not in cat.list_namespaces():
            cat.create_namespace(NAMESPACE)
        return cat

    def filesystem(self) -> tuple[pafs.FileSystem, str]:
        """The bucket (or directory) as a filesystem, to list and remove files the catalog no longer needs."""
        if self.s3:
            u = urlparse(self.warehouse)
            ep = urlparse(self.s3["endpoint"]) if self.s3.get("endpoint") else None
            f = pafs.S3FileSystem(access_key=self.s3.get("key"), secret_key=self.s3.get("secret"), region=self.s3.get("region"),
                                  endpoint_override=ep.netloc if ep else None, scheme=ep.scheme if ep else "https")
            return f, f"{u.netloc}{u.path}".rstrip("/")
        return pafs.LocalFileSystem(), str(Path(self.warehouse).resolve())


def _strip(uri: str) -> str:
    u = urlparse(uri)
    return f"{u.netloc}{u.path}" if u.scheme in ("s3", "s3a") else (u.path if u.scheme == "file" else uri)


def _arrow(con, table: str) -> pa.Table:
    rel = con.sql(f"SELECT * FROM {table} ORDER BY {KEY}")
    t = rel.to_arrow_table() if hasattr(rel, "to_arrow_table") else rel.arrow()   # DuckDB 2.x, then 1.x
    # Iceberg has no unsigned or 128-bit ints and wants microsecond timestamps; bronze has neither problem,
    # but say so here rather than fail inside a commit
    return t.cast(pa.schema([pa.field(f.name, pa.large_string() if pa.types.is_string(f.type) else f.type) for f in t.schema]))


def publish(warehouse: str | Path, lake: Lake, tables=TABLES) -> dict:
    cat = lake.catalog()
    out: dict = {}
    erased_any = False
    with duckdb.connect(str(warehouse), read_only=True) as con:
        for src in tables:
            name = f"{NAMESPACE}.{src}"
            data = _arrow(con, f"bronze_{src}")
            if not cat.table_exists(name):
                cat.create_table(name, schema=data.schema, properties=TABLE_PROPERTIES)
            tbl = cat.load_table(name)
            have = tbl.scan(selected_fields=(KEY,)).to_arrow()[KEY] if tbl.current_snapshot() else pa.array([], pa.large_string())
            new = data.filter(pc.invert(pc.is_in(data[KEY], value_set=have.combine_chunks() if hasattr(have, "combine_chunks") else have)))
            gone = pc.filter(have, pc.invert(pc.is_in(have, value_set=data[KEY].combine_chunks())))
            if len(new):
                tbl.append(new)
            if len(gone):
                from pyiceberg.expressions import In
                tbl.refresh().delete(In(KEY, set(gone.to_pylist())))
                erased_any = True
            tbl = tbl.refresh()
            out[src] = {"appended": len(new), "deleted": len(gone), "rows": _count(tbl)}
    if erased_any:
        out["history"] = forget_history(lake, cat, tables)
    return out


def _count(tbl) -> int:
    return tbl.scan(selected_fields=(KEY,)).to_arrow().num_rows if tbl.current_snapshot() else 0


def forget_history(lake: Lake, cat, tables=TABLES) -> dict:
    """Expire every snapshot but the current one, then remove each data file no snapshot needs."""
    expired = removed = 0
    for src in tables:
        tbl = cat.load_table(f"{NAMESPACE}.{src}")
        keep = tbl.current_snapshot().snapshot_id if tbl.current_snapshot() else None
        old = [s.snapshot_id for s in tbl.snapshots() if s.snapshot_id != keep]
        if old:
            tbl.maintenance.expire_snapshots().by_ids(old).commit()
            expired += len(old)
        removed += _remove_unreferenced(lake, cat.load_table(f"{NAMESPACE}.{src}"))
    return {"snapshots_expired": expired, "files_removed": removed}


def _remove_unreferenced(lake: Lake, tbl) -> int:
    """Remove every file under the table that the current metadata does not need: data files of
    expired snapshots, and their manifests too, because a manifest stores each column's lowest and
    highest value, so a deleted person's address can survive there as a "lower bound"."""
    f, _ = lake.filesystem()
    live = {_strip(tbl.metadata_location)}
    if tbl.current_snapshot():
        live |= {_strip(p) for p in tbl.inspect.all_files().column("file_path").to_pylist()}
        live |= {_strip(p) for p in tbl.inspect.all_manifests().column("path").to_pylist()}
        live |= {_strip(s.manifest_list) for s in tbl.snapshots()}
    n = 0
    for sub in ("data", "metadata"):
        try:
            infos = f.get_file_info(pafs.FileSelector(_strip(tbl.location()) + "/" + sub, recursive=True))
        except FileNotFoundError:
            continue
        for i in infos:
            if i.type == pafs.FileType.File and i.path not in live:
                f.delete_file(i.path)
                n += 1
    return n


def read(lake: Lake, src: str, snapshot_id: int | None = None) -> pa.Table:
    tbl = lake.catalog().load_table(f"{NAMESPACE}.{src}")
    return tbl.scan(snapshot_id=snapshot_id).to_arrow()


def stored_values(lake: Lake) -> tuple[set[str], bytes]:
    """What someone holding the bucket could read: every string value in every Parquet file
    (decompressed), every column bound in every manifest, and the raw bytes of everything else."""
    import pyarrow.parquet as pq
    import fastavro
    f, root = lake.filesystem()
    values, raw = set(), []
    for i in f.get_file_info(pafs.FileSelector(root, recursive=True)):
        if i.type != pafs.FileType.File:
            continue
        with f.open_input_stream(i.path) as s:
            data = s.read()
        raw.append(data)
        if i.path.endswith(".parquet"):
            t = pq.read_table(pa.BufferReader(data))
            for c in t.columns:
                if pa.types.is_string(c.type) or pa.types.is_large_string(c.type):
                    values |= {v for v in c.to_pylist() if v is not None}
        elif i.path.endswith(".avro"):
            import io
            for rec in fastavro.reader(io.BytesIO(data)):
                df = rec.get("data_file") or {}
                for key in ("lower_bounds", "upper_bounds"):
                    for kv in df.get(key) or []:
                        try:
                            values.add(kv["value"].decode("utf-8"))
                        except (UnicodeDecodeError, AttributeError):
                            pass
    return values, b"".join(raw)
