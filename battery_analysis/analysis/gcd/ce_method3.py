"""
Coulombic Efficiency via Adams Method #3 — ported verbatim from
ce_method3 in the original battery_analysis/ce.py. This is the one
function that distinguished ce.py from gcd.py; everything else in ce.py
was the same pipeline as gcd.py (same raw format, same analyze_gcd).

Segments the trace into: stabilisation half-cycle -> QT reservoir plate
-> n exchange cycles -> final stripping (QS), all as Q = current_density x dt.
"""
from __future__ import annotations

import numpy as np


def ce_method3(
    time_h, voltage_v, current_density: float,
    n_cycles: int = 10, stabilisation_hours: float = 10.0,
    qt_hours: float = 10.0, cycle_hours: float = 1.0,
) -> dict:
    t = np.asarray(time_h)

    t_stab_end = stabilisation_hours
    t_qt_end = t_stab_end + qt_hours
    t_cycles_end = t_qt_end + n_cycles * 2 * cycle_hours

    mask_qt = (t > t_stab_end) & (t <= t_qt_end)
    dt_qt = np.diff(t[mask_qt])
    QT = float(current_density * dt_qt.sum()) if mask_qt.sum() > 1 else float("nan")

    cycle_CEs, cycle_Qplate, cycle_Qstrip, Qc_list = [], [], [], []

    for k in range(n_cycles):
        t0_plate = t_qt_end + k * 2 * cycle_hours
        t1_plate = t0_plate + cycle_hours
        t0_strip = t1_plate
        t1_strip = t0_strip + cycle_hours

        mask_p = (t > t0_plate) & (t <= t1_plate)
        mask_s = (t > t0_strip) & (t <= t1_strip)

        dt_p = np.diff(t[mask_p])
        dt_s = np.diff(t[mask_s])

        Qp = float(current_density * dt_p.sum()) if mask_p.sum() > 1 else float("nan")
        Qs = float(current_density * dt_s.sum()) if mask_s.sum() > 1 else float("nan")

        cycle_Qplate.append(Qp)
        cycle_Qstrip.append(Qs)

        ce = (Qs / Qp * 100) if (Qp and Qp > 0) else float("nan")
        cycle_CEs.append(ce)
        if not np.isnan(Qp):
            Qc_list.append(Qp)

    Qc_avg = float(np.nanmean(Qc_list)) if Qc_list else float("nan")

    mask_qs = t > t_cycles_end
    dt_qs = np.diff(t[mask_qs])
    QS = float(current_density * dt_qs.sum()) if mask_qs.sum() > 1 else float("nan")

    numerator = n_cycles * Qc_avg + QS if not np.isnan(QS) else float("nan")
    denominator = n_cycles * Qc_avg + QT if not np.isnan(QT) else float("nan")
    CE_avg = (numerator / denominator * 100) if (denominator and denominator > 0) else float("nan")

    return {
        "QT": QT, "QS": QS, "Qc_avg": Qc_avg, "CE_avg": CE_avg,
        "CE_initial": float("nan"),
        "cycle_CEs": cycle_CEs, "cycle_Qplate": cycle_Qplate, "cycle_Qstrip": cycle_Qstrip,
        "n_cycles": n_cycles,
    }
