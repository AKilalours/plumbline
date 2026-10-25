from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True)
class SynthConfig:
    """Every knob that changes the output. The whole object is written to the manifest."""

    seed: int = 7
    n_people: int = 100_000
    window_start: date = date(2024, 1, 1)
    window_end: date = date(2025, 12, 31)

    # Who appears in which source.
    customer_share: float = 0.35   # people who are Kestrel customers (CRM)
    vendor_coverage: float = 0.60  # people present in the third-party vendor file

    # Life events over the window. Home purchase is modulated by income, age,
    # renting and a recent job change; see docs/synthetic_data_card.md.
    move_rate: float = 0.12
    job_change_rate: float = 0.15
    home_purchase_base_rate: float = 0.04

    # Damage applied when a source "observes" a person.
    typo_rate: float = 0.08
    nickname_rate: float = 0.10
    missing_rate: float = 0.05
    crm_duplicate_rate: float = 0.03
    crm_stale_address_on_move: float = 0.70
    vendor_stale_address_on_move: float = 0.40

    # Deliveries.
    vendor_weeks: int = 8
    vendor_rename_week: int = 5   # job_title becomes occupation from this week on
    vendor_resent_week: int = 6   # this week is re-sent once with corrections
    vendor_repeat_rate: float = 0.05
    email_days: int = 14
    email_late_rate: float = 0.15
    email_corrected_day: int = 7  # this day's file is corrected twice
    deletion_request_rate: float = 0.005

    def to_dict(self) -> dict:
        d = asdict(self)
        d["window_start"] = self.window_start.isoformat()
        d["window_end"] = self.window_end.isoformat()
        return d
