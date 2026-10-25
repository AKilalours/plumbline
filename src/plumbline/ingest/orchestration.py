"""Dagster wrapper around the loader. All logic lives in engine.py; this file only schedules and reports.

    dagster dev -m plumbline.ingest.orchestration

One asset (the warehouse after ingesting every delivered file) and four asset checks, each a promise
from engine.py, evaluated after every run. A failed check blocks nothing silently: it shows red in the
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
    warehouse: str = "data/warehouse.duckdb"
    contracts_dir: str = "sources"


def _violations(cfg: PlumblineConfig, needle: str) -> dict[str, int]:
    con = duckdb.connect(cfg.warehouse, read_only=True)
    try:
        return {k: v for k, v in check_invariants(con, load_contracts(cfg.contracts_dir)).items() if needle in k}
    finally:
        con.close()


@dg.asset(group_name="ingestion", description="Every delivered file loaded once into DuckDB, with ledger, quarantine and suppression.")
def warehouse(context: dg.AssetExecutionContext, plumbline: PlumblineConfig) -> dg.MaterializeResult:
    report = ingest(plumbline.run_dir, plumbline.warehouse, load_contracts(plumbline.contracts_dir))
    totals = report.totals()
    rejected = [r.path for r in report.results if r.status == "rejected"]
    if rejected:
        context.log.warning(f"rejected deliveries: {rejected}")
    return dg.MaterializeResult(metadata={**{k: dg.MetadataValue.int(v) for k, v in totals.items()},
                                          "rejected_files": dg.MetadataValue.json(rejected)})


def _check(name: str, needle: str, description: str):
    @dg.asset_check(asset=warehouse, name=name, description=description)
    def _c(plumbline: PlumblineConfig) -> dg.AssetCheckResult:
        v = _violations(plumbline, needle)
        return dg.AssetCheckResult(passed=sum(v.values()) == 0,
                                   metadata={k: dg.MetadataValue.int(n) for k, n in v.items()})
    return _c


checks = [
    _check("one_current_row_per_key", "duplicate_current_keys", "Each key has exactly one current row."),
    _check("no_row_stored_twice", "duplicate_stored_rows", "Re-sends and replays never duplicate stored rows."),
    _check("deleted_people_stay_deleted", "suppressed", "No row in bronze or quarantine matches a deletion request."),
    _check("every_row_has_a_loaded_file", "without_a_loaded_delivery", "Every stored row traces to a loaded delivery."),
]

ingest_job = dg.define_asset_job("ingest_job", selection=[warehouse])
nightly = dg.ScheduleDefinition(job=ingest_job, cron_schedule="0 2 * * *", execution_timezone="America/New_York")

defs = dg.Definitions(
    assets=[warehouse],
    asset_checks=checks,
    jobs=[ingest_job],
    schedules=[nightly],
    resources={"plumbline": PlumblineConfig(
        run_dir=os.environ.get("PLUMBLINE_RUN_DIR", "data/synth/seed7_n100000"),
        warehouse=os.environ.get("PLUMBLINE_WAREHOUSE", "data/warehouse.duckdb"),
        contracts_dir=os.environ.get("PLUMBLINE_CONTRACTS", "sources"))},
)
