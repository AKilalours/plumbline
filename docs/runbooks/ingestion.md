# Runbook: ingestion

For whoever is on call. Every command runs from the repo root with the virtual environment active.
Check the state first, every time:

```bash
python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb
```

It prints load totals and any invariant violations, and exits 1 if there are violations.

## 1. A file was rejected

Symptom: `deliveries_rejected` above 0, a red `warehouse` run in Dagster, or a ledger row with status `rejected`.

```bash
duckdb data/warehouse.duckdb "SELECT path, note FROM ingest_ledger WHERE status = 'rejected'"
```

- `missing_columns:<name>`: the sender dropped or renamed a column. Nothing from the file was loaded, on
  purpose: loading it would overwrite good values with blanks. If the column was renamed for good, add
  `aliases: {new_name: old_name}` to `sources/<source>.yaml` and ask the sender to re-send. A re-send with
  identical bytes is skipped, so the corrected file needs a new name or a `_v2` suffix.
- `not_utf8`, `empty_file`, `duplicate_columns`: ask the sender for a clean re-send.

## 2. Quarantine is growing

```bash
duckdb data/warehouse.duckdb "SELECT source, reason, count(*) FROM quarantine GROUP BY 1, 2 ORDER BY 3 DESC"
```

- `duplicate_in_delivery`: expected in small numbers (the email platform repeats events). No action.
- `bad_field_count`: a truncated or malformed file. If one file accounts for most of it, ask for a re-send.
- `bad_<type>:<column>`: the sender changed a format. Fix the contract only if the new format is legitimate.

Quarantined rows never reach the current tables. Nothing needs cleaning up after a fix: the corrected
re-send loads normally.

## 3. A deletion request arrives

Nothing to do by hand. Loading the privacy file purges matching rows from every bronze table and from
quarantine, and blocks them in all later loads, including replays of old files. Confirm with:

```bash
duckdb data/warehouse.duckdb "SELECT source, action, sum(rows) FROM suppression_log GROUP BY 1, 2"
```

Known limit: matching is on exact email and phone. A record with a typo in its email and no phone is
not caught (8 of 500 deleted people on the seed-7 run). Phase 2 identity matching closes this.

## 4. A load crashed halfway

Rerun the same command. Each file loads in one transaction together with its ledger row, so a crash
leaves no partial file behind, and the rerun picks up exactly the files that did not finish.

## 5. The store sync failed

- `AuthError (401)`: the token is wrong or revoked. Do not retry in a loop; fix `STORE_API_TOKEN`.
- `gave up after N retries`: the store was down longer than the backoff budget. Nothing is lost:
  the watermark did not move and `sources/store/_sync_state.json` holds the last good cursor.
  Rerun the sync later and it resumes from that page.
- To re-pull everything (for example after the store fixed bad data on its side), delete
  `_sync_state.json` only. The loader skips every row it already has, so this costs time, not correctness.

## 6. The deletion review queue is not empty

`deletion_review` lists persons that might be a leftover of a deleted person but where the evidence
is not enough to delete automatically:
- `one_key_matches`: same first name, last name and zip as a deleted person's purged row, nothing else.
  Usually a namesake. Delete only if another source confirms it (same work email domain, a store
  order shipped to the deleted person's old address).
- `tombstone_matches_several_persons`: one deleted row fits two current persons. Never delete both.

Seed 7 baseline: 7 persons queued, 1 of them a real leftover. A sudden jump means the identity model
or a source changed; check `identity_pairs` for the persons involved before acting.

## 7. Last resort: rebuild from scratch

```bash
mv data/warehouse.duckdb data/warehouse.broken.duckdb
python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb
```

The rebuild reproduces the current tables and quarantine exactly (tested in
`test_rebuild_from_scratch_is_identical`). Keep the broken file until the cause is understood.
