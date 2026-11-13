"""
dQ/dV calculation — ported verbatim from calculate_dqdv /
_extract_cycle_from_csv in the original battery_analysis/dqdv.py.

Reuses cycling_common's profile-CSV reading (the "*_Cycle.csv" files
already discovered for the Cycle technique) — no new raw-file reader.
extract_cycle_profile() itself (the column-matching raw-trace reader)
now lives in cycling_common.io, shared with the Cycle/Rate
voltage-vs-capacity profile plots, which need the exact same trace with
no dQ/dV-specific smoothing on top.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.signal import savgol_filter

from battery_analysis.analysis.cycling_common.io import extract_cycle_profile, ordinal

__all__ = ["ordinal", "calculate_dqdv", "extract_cycle_dqdv"]


def calculate_dqdv(capacity, voltage, smoothing_window: int = 51):
    """
    dQ/dV from raw capacity/voltage arrays, in original measurement order
    (not sorted by voltage — sorting destroys the physical trajectory and
    creates spiking artefacts on flat plateaux). Savitzky-Golay smoothing
    then np.gradient, exactly as the original.
    """
    mask = ~(np.isnan(capacity) | np.isnan(voltage))
    cap = np.asarray(capacity)[mask]
    volt = np.asarray(voltage)[mask]
    if len(volt) == 0:
        return None, None

    win = int(smoothing_window)
    if win % 2 == 0:
        win += 1
    win = min(win, len(cap) if len(cap) % 2 == 1 else len(cap) - 1)
    if win < 5:
        return None, None

    try:
        cap_smooth = savgol_filter(cap, win, polyorder=2)
        dcap = np.gradient(cap_smooth)
        dvolt = np.gradient(volt)
        valid = np.isfinite(dcap) & np.isfinite(dvolt) & (np.abs(dvolt) > 1e-6)
        if valid.sum() < 5:
            return None, None

        dqdv = dcap[valid] / dvolt[valid]
        volt_valid = volt[valid]
        finite = np.isfinite(dqdv) & np.isfinite(volt_valid)
        if finite.sum() < 5:
            return None, None

        return volt_valid[finite], dqdv[finite]
    except Exception as e:
        print(f"dQ/dV error: {e}")
        return None, None


def extract_cycle_dqdv(profile_path, cycle_num: int, mode: str, smoothing_window: int = 51):
    """
    Load one ordinal cycle's charge or discharge dQ/dV from a *_Cycle.csv
    profile file. mode: "charge" | "discharge". Cathode convention (no
    instrument-mode swap — that's an anode-only concern, same as rate.py).
    """
    voltage, capacity = extract_cycle_profile(profile_path, cycle_num, mode)
    if voltage is None:
        return None, None
    return calculate_dqdv(capacity, voltage, smoothing_window)
