"""
Rate-specific raw CSV reading — ported verbatim from generate_rate_excel
in the original battery_analysis/rate.py. Kept separate from
cycling_common/io.py's read_capacity_csv because the column handling
genuinely differs: no "Cycle" column requirement, an electrode_type
swap, and a synthetic sequential Rate_Cycle index instead of the
instrument's own cycle numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from battery_analysis.analysis.cycling_common.io import _find_column, CyclingFileFormatError
from battery_analysis.analysis.cycling_common.models import CyclingSeries


def load_series(sample_name: str, capacity_path, electrode_type: str = "cathode") -> CyclingSeries | None:
    # Same whitespace/case-insensitive column matching as
    # cycling_common.io.read_capacity_csv, and the same "skip this one
    # file, don't crash the batch" contract — kept as its own reader
    # (see module docstring) rather than reusing that function outright,
    # since Rate genuinely doesn't need its Cycle-column requirement.
    try:
        df = pd.read_csv(capacity_path)
        df.columns = [c.strip() for c in df.columns]
        q_dis_col = _find_column(df, "Q_Dis/M [mAh/g]", capacity_path)
        q_ch_col = _find_column(df, "Q_Ch/M [mAh/g]", capacity_path)
    except CyclingFileFormatError as e:
        print(f"⚠️  {e}")
        return None

    q_dis, q_ch = df[q_dis_col].copy(), df[q_ch_col].copy()
    if electrode_type == "anode":
        # Anode: charge = lithiation = what the instrument calls discharge -> swap.
        q_ch, q_dis = q_dis, q_ch

    df["Q_Ch/M [mAh/g]"] = q_ch
    df["Q_Dis/M [mAh/g]"] = q_dis
    df["CE (%)"] = 100 * df["Q_Dis/M [mAh/g]"] / df["Q_Ch/M [mAh/g]"]
    if df.empty:
        return None

    return CyclingSeries(
        sample_name=sample_name,
        cycle_number=np.arange(len(df)),   # "Rate_Cycle" — sequential, not the instrument's own cycle numbers
        capacity_charge=df["Q_Ch/M [mAh/g]"].to_numpy(),
        capacity_discharge=df["Q_Dis/M [mAh/g]"].to_numpy(),
        ce_pct=df["CE (%)"].to_numpy(),
        source_file=capacity_path,
    )
