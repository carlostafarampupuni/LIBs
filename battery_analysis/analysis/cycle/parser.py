"""Cycle-specific parsing: read_capacity_csv -> CyclingSeries."""
from __future__ import annotations

from battery_analysis.analysis.cycling_common.io import read_capacity_csv, CyclingFileFormatError
from battery_analysis.analysis.cycling_common.models import CyclingSeries


def load_series(sample_name: str, capacity_path) -> CyclingSeries | None:
    # A malformed/unexpected file (missing column, unreadable CSV) skips
    # with a warning instead of aborting the whole batch import — one
    # bad file shouldn't cost you every other sample's data.
    try:
        df = read_capacity_csv(capacity_path)
    except CyclingFileFormatError as e:
        print(f"⚠️  {e}")
        return None
    if df.empty:
        return None
    return CyclingSeries(
        sample_name=sample_name,
        cycle_number=df["Cycle"].to_numpy(),
        capacity_charge=df["Q_Charge"].to_numpy(),
        capacity_discharge=df["Q_Discharge"].to_numpy(),
        ce_pct=df["CE (%)"].to_numpy(),
        source_file=capacity_path,
    )
