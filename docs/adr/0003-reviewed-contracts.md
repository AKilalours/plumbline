# 0003 · Every source has a reviewed contract; nothing loads on a guess

*2026-10; amended 2026-11-12 (approved columns reach the connector)*

**Context.** Sources change without notice: columns appear, vendors rename fields, formats drift. A model can
draft a contract for a new partner file in seconds and gets most of it right.

**Decision.** One YAML contract per source (types, key, version order, privacy, identity roles, a delivery gate)
is the only way data enters. A drafted contract goes to a drafts folder (sources/drafts/, made on first use) for a person; a column not in the
contract is reported and held back; a row that breaks it is quarantined with a reason; a whole delivery that
looks wrong against its history is held until a named person releases it with a reason.

**Rejected.** Schema-on-read (load everything, sort it out later): bad rows reach models before anyone looks.
Auto-applying a drafted contract: on the held-out partner files the model gets 30 of 34 column types right, the
rules 31; neither is good enough to apply unseen.

**Costs.** A person in the loop for every new source and column; a held column is data not yet used.

**The amendment.** The connector chose its column set from the first page it read, without the contract; NYC's
API leaves out a column empty on a whole page, so an approved column (apartment numbers) was called drift and
dropped for five days with every run green. Connectors now receive the contract's columns
(`test_a_column_the_contract_approves_is_written_when_it_first_appears_after_the_first_page`).

**Evidence.** `tests/test_ingest.py` (quarantine reasons, drift held back), `tests/test_gate.py` (0 of 276 normal
deliveries held; truncated and emptied files held), `evals/onboarding.yaml`.

**Revisit when** contracts change faster than people can review them; then the gate's limits, not a person,
should approve the routine part.
