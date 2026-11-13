"""
Cathode dQ/dV phase-transition peak finding — adapted from
dQdVAnalyzer.find_peaks in the original battery_analysis/dqdv.py.

The original extracts charge and discharge as separate curves per cycle
(_extract_cycle_from_csv takes a `mode` argument) and expects: charge
(lithiation) produces positive dQ/dV peaks, discharge (delithiation)
produces negative dQ/dV peaks (reported here as a positive intensity,
matching the original's convention). Since each mode is stored as its
own curve, peak-finding here is applied per-mode with that expected
sign, rather than splitting one combined array into both directions.

Note: dQdVComparator (cross-cycle peak-shift tracking / new-vs-lost peak
detection) is NOT yet ported — this covers per-cycle peak finding, which
is what the story report and plots need.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks as _find_peaks

from .models import DQDVPeak


def find_peaks(voltage, dqdv, mode: str, prominence=None):
    """Peaks matching the expected sign for `mode` ("charge" -> positive
    dQ/dV, "discharge" -> negative dQ/dV, intensity reported positive)."""
    voltage = np.asarray(voltage)
    dqdv = np.asarray(dqdv)
    if len(dqdv) == 0:
        return []

    if prominence is None:
        prominence = np.max(np.abs(dqdv)) * 0.1

    signal = dqdv if mode.lower() in ("charge", "ch") else -dqdv
    idx, _ = _find_peaks(signal, prominence=prominence)
    return [DQDVPeak(voltage=float(voltage[i]), intensity=float(signal[i])) for i in idx]


def narrative(sample_name: str, cycle_number: int, mode: str, peaks: list, top_n: int = 5) -> str:
    """
    Only the top_n peaks by |intensity| are shown — raw dQ/dV near a
    charge/discharge voltage cutoff is extremely noisy (current tapering
    at the CC-CV limit produces dozens of spurious near-duplicate local
    maxima that aren't real phase-transition features), and dumping every
    one of them isn't useful to read. The full set is still in the
    database (repo.load_dqdv_peaks) if you want it.
    """
    lines = [f"dQ/dV — {sample_name}, cycle {cycle_number} ({mode})"]
    if not peaks:
        lines.append("  No peaks resolved above the prominence threshold.")
        return "\n".join(lines)

    top = sorted(peaks, key=lambda p: -abs(p.intensity))[:top_n]
    for p in sorted(top, key=lambda p: p.voltage):
        lines.append(f"  Peak at {p.voltage:.3f} V (intensity {p.intensity:.4f})")
    if len(peaks) > top_n:
        lines.append(
            f"  ({len(peaks) - top_n} more, mostly clustered at the voltage cutoff — "
            f"see repo.load_dqdv_peaks() for all of them)"
        )
    return "\n".join(lines)
