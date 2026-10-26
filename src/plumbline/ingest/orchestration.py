"""Dagster wrapper around the loader. All logic lives in engine.py; this file only schedules and reports.

    dagster dev -m plumbline.ingest.orchestration

Three assets in order: the warehouse after ingesting every delivered file, persons (identity
resolution) and person-level deletion. Four asset checks on the warehouse, each a promise from
engine.py, evaluated after every run. A failed check blocks nothing silently: it shows red in the
Dagster UI and its counts are attached as metadata.
"""

# No `from __future__ import annotations` in this file: Dagster inspects type hints at
# definition time and cannot resolve them when they are stored as strings.
import os

import dagster as dg
import duckdb

from .checks import check_invariants
from .contracts import load_contracts
from .engine import ingest


class PlumblineConfig(dg.ConfigurableResource):
    run_dir: str = "data/synth/seed7_n100000"
    reference_dir: str = "data/reference"
    models_dir: str = "data/models/home_buyer"
    warehouse: str = "data/warehouse.duckdb"
    contracts_dir: str = "sources"


def _violations(cfg: PlumblineConfig, needles: tuple[str, ...]) -> dict[str, int]:
    con = duckdb.connect(cfg.warehouse, read_only=True)
    try:
        v = check_invariants(con, load_contracts(cfg.contracts_dir))
    finally:
        con.close()
    found = {k: n for k, n in v.items() if any(x in k for x in needles)}
    if not found:  # a renamed counter must fail loudly, not turn the check silently green
        raise RuntimeError(f"no invariant matches {needles}")
    return found


@dg.asset(group_name="ingestion", description="Every delivered file loaded once into DuckDB, with ledger, quarantine and suppression.")
def warehouse(context: dg.AssetExecutionContext, plumbline: PlumblineConfig) -> dg.MaterializeResult:
    report = ingest(plumbline.run_dir, plumbline.warehouse, load_contracts(plumbline.contracts_dir))
    totals = report.totals()
    rejected = [r.path for r in report.results if r.status == "rejected"]
    if rejected:
        context.log.warning(f"rejected deliveries: {rejected}")
    return dg.MaterializeResult(metadata={**{k: dg.MetadataValue.int(v) for k, v in totals.items()},
                                          "rejected_files": dg.MetadataValue.json(rejected)})


def _check(name: str, needles: tuple[str, ...], description: str):
    @dg.asset_check(asset=warehouse, name=name, description=description)
    def _c(plumbline: PlumblineConfig) -> dg.AssetCheckResult:
        v = _violations(plumbline, needles)
        return dg.AssetCheckResult(passed=sum(v.values()) == 0,
                                   metadata={k: dg.MetadataValue.int(n) for k, n in v.items()})
    return _c


checks = [
    _check("one_current_row_per_key", ("duplicate_current_keys",), "Each key has exactly one current row."),
    _check("no_row_stored_twice", ("duplicate_stored_rows",), "Re-sends and replays never duplicate stored rows."),
    _check("deleted_people_stay_deleted", ("erasable_rows_in_bronze", "quarantine.suppressed_rows"),
           "No row in bronze or quarantine matches a deletion request."),
    _check("every_row_has_a_loaded_file", ("without_a_loaded_delivery",), "Every stored row traces to a loaded delivery."),
]


@dg.asset(group_name="identity", deps=[warehouse],
          description="Records from every source resolved into persons (Splink + cannot-link clustering).")
def persons(plumbline: PlumblineConfig) -> dg.MaterializeResult:
    from ..identity.pipeline import resolve
    with duckdb.connect(plumbline.warehouse) as con:
        out = resolve(con)
    return dg.MaterializeResult(metadata={k: dg.MetadataValue.int(v) if isinstance(v, int) else dg.MetadataValue.float(float(v))
                                          for k, v in out.items() if v is not None})


@dg.asset(group_name="privacy", deps=[persons],
          description="Deletion requests applied to whole persons: tombstone matches plus the identity graph.")
def person_level_deletion(plumbline: PlumblineConfig) -> dg.MaterializeResult:
    from ..privacy.person_deletion import expand_deletions
    with duckdb.connect(plumbline.warehouse) as con:
        out = expand_deletions(con, load_contracts(plumbline.contracts_dir))
    return dg.MaterializeResult(metadata={"persons_deleted": dg.MetadataValue.int(out["persons_deleted"]),
                                          "held_for_review": dg.MetadataValue.int(out["held_for_review"]),
                                          "rows_removed": dg.MetadataValue.json(out["rows_removed"])})


@dg.asset(group_name="products", deps=[person_level_deletion],
          description="dbt build: golden person, address history, life events, households, activation audience, and their tests.")
def data_products(plumbline: PlumblineConfig) -> dg.MaterializeResult:
    from ..ml.reference import load_reference_tables
    from ..products.build import build
    with duckdb.connect(plumbline.warehouse) as con:
        load_reference_tables(con, plumbline.reference_dir)
    out = build(plumbline.warehouse)
    return dg.MaterializeResult(metadata={k: dg.MetadataValue.int(v) for k, v in out.items()})


@dg.asset(group_name="models", deps=[data_products],
          description="Home-buyer model: point-in-time snapshots, training, model card; scores published only if guardrails pass.")
def home_buyer_scores(plumbline: PlumblineConfig) -> dg.MaterializeResult:
    from ..ml.pipeline import train_and_score
    out = train_and_score(plumbline.warehouse, plumbline.run_dir, plumbline.models_dir)
    return dg.MaterializeResult(metadata={k: (dg.MetadataValue.float(float(v)) if isinstance(v, float) else dg.MetadataValue.text(str(v)))
                                          for k, v in out.items()})


ingest_job = dg.define_asset_job("ingest_job", selection=[warehouse, persons, person_level_deletion, data_products, home_buyer_scores])
nightly = dg.ScheduleDefinition(job=ingest_job, cron_schedule="0 2 * * *", execution_timezone="America/New_York")

defs = dg.Definitions(
    assets=[warehouse, persons, person_level_deletion, data_products, home_buyer_scores],
    asset_checks=checks,
    jobs=[ingest_job],
    schedules=[nightly],
    resources={"plumbline": PlumblineConfig(
        run_dir=os.environ.get("PLUMBLINE_RUN_DIR", "data/synth/seed7_n100000"),
        warehouse=os.environ.get("PLUMBLINE_WAREHOUSE", "data/warehouse.duckdb"),
        contracts_dir=os.environ.get("PLUMBLINE_CONTRACTS", "sources"),
        reference_dir=os.environ.get("PLUMBLINE_REFERENCE", "data/reference"),
        models_dir=os.environ.get("PLUMBLINE_MODELS", "data/models/home_buyer"))},
)
