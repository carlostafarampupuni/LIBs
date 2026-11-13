"""
GCD (Li-Li plating/stripping) analysis — segmentation, nucleation
metrics, stripping metrics, spike detection, and the mechanism-assessment
narrative are copied verbatim (behaviour-identical) from analyze_gcd in
the original battery_analysis/gcd.py. Only the entry point changed: this
takes plain (time_h, voltage_v) arrays (loaded from the database) instead
of being called directly on a freshly-read CSV.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _segment_halfcycles(x, y, plating_sign=-1, min_points=10):
    """Split (x, y) into alternating plating / stripping segments."""
    x = pd.Series(x).reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True)

    v_mean = float(y.mean())
    side = np.sign(y.values - v_mean).astype(int)
    side[side == 0] = plating_sign

    flip = np.where(np.diff(side) != 0)[0] + 1
    bounds = np.concatenate([[0], flip, [len(y)]])

    segs = []
    for k in range(len(bounds) - 1):
        sl = slice(bounds[k], bounds[k + 1])
        sx, sy = x.iloc[sl], y.iloc[sl]
        if len(sy) < min_points:
            continue
        is_plating = (side[bounds[k]] == plating_sign)
        segs.append((sx.reset_index(drop=True), sy.reset_index(drop=True), is_plating))
    return segs


def _segment_by_phase(x, y, phase, min_points=10):
    """
    Same output as _segment_halfcycles, but the half-cycles come from the
    cycler's own step labels (NEWARE: CC DChg = plating, CC Chg = stripping)
    instead of the voltage sign — needed for Na‖Al / Na‖Cu cells, where
    stripping sits only a few mV above 0 V before the jump to the cut-off.
    """
    x = pd.Series(x).reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True)
    p = pd.Series(phase).reset_index(drop=True)
    run = (p != p.shift()).cumsum()
    segs = []
    for _, idx in p.groupby(run).groups.items():
        if len(idx) < min_points:
            continue
        segs.append((x.iloc[idx].reset_index(drop=True), y.iloc[idx].reset_index(drop=True),
                     p.iloc[idx[0]] == "plating"))
    return segs


def _pair_cycles(segs):
    """Pair consecutive (plate, strip) segments into cycles."""
    cycles = []
    i = 0
    while i < len(segs) - 1:
        ax, ay, a_is_plate = segs[i]
        bx, by, b_is_plate = segs[i + 1]
        if a_is_plate and not b_is_plate:
            cycles.append(((ax, ay), (bx, by)))
            i += 2
        elif not a_is_plate and b_is_plate:
            cycles.append(((bx, by), (ax, ay)))
            i += 2
        else:
            i += 1
    return cycles


def _nucleation_metrics(px, py, plating_sign, nuc_frac, plateau_frac):
    n = len(py)
    n_nuc = max(int(n * nuc_frac), 3)
    n_confirm = max(int(n * 0.05), 3)
    n_plateau = max(int(n * plateau_frac), 3)

    nuc_window = py.iloc[:n_nuc]
    plateau_window = py.iloc[-n_plateau:]

    nuc_idx = nuc_window.idxmin() if plating_sign == -1 else nuc_window.idxmax()

    V_nuc = float(py.iloc[nuc_idx])
    t_nuc = float(px.iloc[nuc_idx])
    V_plateau = float(plateau_window.mean())

    confirm_end = min(nuc_idx + n_confirm, n)
    if confirm_end > nuc_idx + 1:
        post_dip = py.iloc[nuc_idx + 1: confirm_end]
        recovery = float((post_dip - V_nuc).abs().mean())
        is_genuine_dip = recovery > 0.002
    else:
        is_genuine_dip = False

    eta_nuc = abs(V_nuc - V_plateau)
    return t_nuc, V_nuc, V_plateau, eta_nuc, is_genuine_dip


def _stripping_metrics(sx, sy, plating_sign, plateau_frac, strip_plateau="end"):
    n = len(sy)
    lo = max(int(n * 0.20), 3)
    hi = min(int(n * 0.80), n - 3)
    n_plateau = max(int(n * plateau_frac), 3)

    mid_y = sy.iloc[lo:hi]
    pol_idx = mid_y.idxmax() if plating_sign == -1 else mid_y.idxmin()

    t_pol = float(sx.iloc[pol_idx])
    V_pol = float(sy.iloc[pol_idx])
    if strip_plateau == "middle":   # stripping to a cut-off: the end is the jump, not a plateau
        V_plateau_s = float(sy.iloc[lo:hi].median())
    else:
        V_plateau_s = float(sy.iloc[-n_plateau:].mean())
    V_early = float(sy.iloc[:max(int(n * 0.05), 2)].mean())

    total_swing = float(sy.max() - sy.min()) if plating_sign == -1 else float(sy.min() - sy.max())

    if total_swing < 0.005:
        shape = "flat"
    elif abs(V_pol - V_plateau_s) >= 0.005 and abs(V_pol - V_early) >= 0.005:
        shape = "peaked"
    else:
        shape = "rising"

    return t_pol, V_pol, V_plateau_s, shape


def _detect_spikes(x, y, window=10, z_thresh=3.5):
    if len(y) < window * 2:
        return []

    dv = y.diff().abs().fillna(0)
    roll_mean = dv.rolling(window, center=True).mean().fillna(dv.mean())
    roll_std = dv.rolling(window, center=True).std().fillna(dv.std())
    roll_std = roll_std.replace(0, 1e-9)

    z = (dv - roll_mean) / roll_std
    spike_idx = z[z > z_thresh].index

    spikes = []
    used = set()
    for idx in spike_idx:
        if idx in used:
            continue
        local = range(max(0, idx - 5), min(len(y), idx + 6))
        peak = max(local, key=lambda i: z.iloc[i] if i < len(z) else 0)
        used.update(local)
        spikes.append((float(x.iloc[peak]), float(y.iloc[peak]), float(dv.iloc[peak])))
    return spikes


def analyze_gcd(
    x, y, label, current_density_mA_cm2,
    plating_sign=-1, nuc_frac=0.15, plateau_frac=0.25,
    spike_z_thresh=3.5, min_halfcycle_points=10, phase=None, strip_plateau="end",
    cutoff_v=None,
):
    """
    Returns (df, anomalies) exactly as the original — one row per cycle
    with plating/stripping capacity, CE, nucleation/stripping/hysteresis
    metrics, plus a list of (cycle, phase, t, V, dV) voltage anomalies.

    phase (optional, NEWARE files): "plating"/"stripping" per point — the
    half-cycles are then taken from it instead of the voltage sign.
    """
    if phase is not None:
        segs = _segment_by_phase(x, y, phase, min_halfcycle_points)
    else:
        segs = _segment_halfcycles(x, y, plating_sign, min_halfcycle_points)
    cycles = _pair_cycles(segs)

    if not cycles:
        return pd.DataFrame(), []

    records = []
    anomalies = []
    Q_strip_c1 = None

    for cyc, ((px, py), (sx, sy)) in enumerate(cycles, start=1):
        dt_p = float(px.diff().abs().sum())
        dt_s = float(sx.diff().abs().sum())
        Q_p = current_density_mA_cm2 * dt_p
        Q_s = current_density_mA_cm2 * dt_s
        CE = (Q_s / Q_p * 100) if Q_p > 0 else float("nan")
        LLI = Q_p - Q_s

        if Q_strip_c1 is None:
            Q_strip_c1 = Q_s if Q_s > 0 else float("nan")
        cap_ret = (Q_s / Q_strip_c1 * 100) if Q_strip_c1 else float("nan")

        t_nuc, V_nuc, V_plat_p, eta_nuc, genuine_dip = _nucleation_metrics(
            px, py, plating_sign, nuc_frac, plateau_frac)
        # The cut-off point (jump to cutoff_v at the end of stripping) stays in
        # the trace and the capacity, but is not part of the stripping plateau,
        # and it is the expected end of the step, not a voltage spike.
        if cutoff_v is not None:
            n_tail = 0
            while n_tail < len(sy) - min_halfcycle_points and abs(sy.iloc[-1 - n_tail]) >= cutoff_v - 0.05:
                n_tail += 1
            if n_tail:
                sx, sy = sx.iloc[:-n_tail].reset_index(drop=True), sy.iloc[:-n_tail].reset_index(drop=True)
        t_pol, V_pol, V_plat_s, strip_shape = _stripping_metrics(
            sx, sy, plating_sign, plateau_frac, strip_plateau)
        hysteresis = abs(V_plat_p - V_plat_s)

        for seg_x, seg_y, seg_name in [(px, py, "plating"), (sx, sy, "stripping")]:
            for t_sp, V_sp, dV_sp in _detect_spikes(seg_x, seg_y, z_thresh=spike_z_thresh):
                anomalies.append((cyc, seg_name, t_sp, V_sp, dV_sp))

        records.append({
            "cycle": cyc,
            "Q_plate (mAh/cm2)": round(Q_p, 5),
            "Q_strip (mAh/cm2)": round(Q_s, 5),
            "CE (%)": round(CE, 2) if CE == CE else float("nan"),
            "LLI (mAh/cm2)": round(LLI, 5),
            "LLI_cumul (mAh/cm2)": None,
            "cap_ret (%)": round(cap_ret, 2) if cap_ret == cap_ret else float("nan"),
            "t_nucleation (h)": round(t_nuc, 4),
            "V_nucleation (V)": round(V_nuc, 4),
            "genuine_nuc_dip": genuine_dip,
            "V_plateau_plate (V)": round(V_plat_p, 4),
            "eta_nuc (V)": round(eta_nuc, 4),
            "t_pol_max (h)": round(t_pol, 4),
            "V_pol_max (V)": round(V_pol, 4),
            "V_plateau_strip (V)": round(V_plat_s, 4),
            "strip_shape": strip_shape,
            "hysteresis (V)": round(hysteresis, 4),
        })

    df = pd.DataFrame(records)
    df["LLI_cumul (mAh/cm2)"] = df["LLI (mAh/cm2)"].cumsum().round(5)
    return df, anomalies


def _slope(df, col):
    c = df[col].dropna()
    if len(c) < 2:
        return float("nan")
    return float(np.polyfit(np.arange(len(c), dtype=float), c.values, 1)[0])


def narrative(df: pd.DataFrame, anomalies: list, label: str, current_density_mA_cm2: float) -> str:
    """
    The mechanism-assessment text — copied verbatim (same thresholds,
    same wording) from analyze_gcd's verbose-print branch in the original.
    """
    if df.empty:
        return f"{label}: no complete plating/stripping cycles detected."

    mean_CE = df["CE (%)"].mean()
    mean_eta = df["eta_nuc (V)"].mean()
    mean_hyst = df["hysteresis (V)"].mean()
    total_LLI = df["LLI_cumul (mAh/cm2)"].iloc[-1]
    final_ret = df["cap_ret (%)"].iloc[-1]
    frac_genuine = df["genuine_nuc_dip"].mean()
    CE_slope = _slope(df, "CE (%)")
    eta_slope = _slope(df, "eta_nuc (V)")
    hyst_slope = _slope(df, "hysteresis (V)")

    if not df["genuine_nuc_dip"].any():
        nuc_shape = (
            f"no confirmed nucleation dip across all cycles (eta_nuc ~ {mean_eta*1000:.0f} mV, "
            f"but voltage does not recover after the minimum) -> templated growth."
        )
    elif mean_eta < 0.015:
        nuc_shape = f"very shallow nucleation dip (eta_nuc ~ {mean_eta*1000:.0f} mV, genuine in {frac_genuine*100:.0f}% of cycles) -> minimal barrier."
    elif mean_eta < 0.050:
        nuc_shape = f"moderate nucleation dip (eta_nuc ~ {mean_eta*1000:.0f} mV, genuine in {frac_genuine*100:.0f}% of cycles) -> mixed morphology."
    else:
        nuc_shape = f"deep nucleation dip (eta_nuc ~ {mean_eta*1000:.0f} mV, genuine in {frac_genuine*100:.0f}% of cycles) -> sparse nucleation."

    if len(df) >= 2:
        if eta_slope > 0.005:
            eta_trend = f"eta_nuc increases {eta_slope*1000:.2f} mV/cycle -> nucleation-site depletion."
        elif eta_slope < -0.005:
            eta_trend = f"eta_nuc decreases {abs(eta_slope)*1000:.2f} mV/cycle -> site multiplication."
        else:
            eta_trend = "eta_nuc stable across cycles."
    else:
        eta_trend = "Single cycle — no trend available."

    dominant_strip_shape = df["strip_shape"].mode().iloc[0]
    mean_V_pol = df["V_pol_max (V)"].mean()
    mean_V_ps = df["V_plateau_strip (V)"].mean()

    if dominant_strip_shape == "flat":
        strip_shape_txt = "stripping profile is flat -> bulk-dominated dissolution."
    elif dominant_strip_shape == "peaked":
        delta_mv = abs(mean_V_pol - mean_V_ps) * 1000
        strip_shape_txt = (
            f"stripping shows polarisation maximum at V ~ {mean_V_pol:.4f} V then relaxes {delta_mv:.0f} mV -> dendrite tips stripped first."
        )
    else:
        strip_shape_txt = "stripping voltage rises monotonically -> SEI passivation impeding dissolution."

    if mean_hyst < 0.03:
        hyst_note = f"plate-strip hysteresis is low ({mean_hyst*1000:.0f} mV) -> near-reversible."
    elif mean_hyst < 0.08:
        hyst_note = f"plate-strip hysteresis is moderate ({mean_hyst*1000:.0f} mV)."
    else:
        hyst_note = f"plate-strip hysteresis is large ({mean_hyst*1000:.0f} mV) -> mossy/porous."

    if mean_CE > 98:
        ce_note = f"CE = {mean_CE:.2f}% -> highly reversible; LLI = {total_LLI:.5f} mAh/cm2."
    elif mean_CE > 90:
        ce_note = f"CE = {mean_CE:.2f}% -> moderate reversibility; LLI = {total_LLI:.5f} mAh/cm2."
    else:
        ce_note = f"CE = {mean_CE:.2f}% -> poor reversibility; LLI = {total_LLI:.5f} mAh/cm2."

    if CE_slope < -0.5:
        ce_fade = f"CE declines {abs(CE_slope):.3f}%/cycle -> accelerating fade."
    elif CE_slope > 0.3:
        ce_fade = f"CE improves {CE_slope:.3f}%/cycle -> conditioning."
    else:
        ce_fade = "CE is steady across cycles."

    if mean_CE > 97 and mean_eta < 0.03 and mean_hyst < 0.05 and not anomalies:
        mech = "Low polarisation, high reversibility. Coating effective."
    elif anomalies:
        mech = "Voltage-spike anomalies -> dendrite shorting / dead-Li events."
    elif mean_CE < 90:
        mech = "Low CE -> continuous dead-Li + SEI re-formation."
    elif eta_slope > 0.005 and hyst_slope > 0.003:
        mech = "Coupled eta_nuc + hysteresis growth -> mossy self-amplifying degradation."
    elif dominant_strip_shape == "rising" and mean_CE < 95:
        mech = "Rising stripping + moderate CE loss -> SEI passivation trapping dead Li."
    else:
        mech = "Moderate — no dominant failure mode yet."

    lines = [
        f"GCD — {label} ({current_density_mA_cm2} mA/cm2, {len(df)} cycle(s))",
        f"  Electrodeposition: {nuc_shape} {eta_trend}",
        f"  Electrodissolution: {strip_shape_txt}",
        f"  Asymmetry: {hyst_note}",
        f"  CE & LLI: {ce_note} {ce_fade} Final capacity retention: {final_ret:.2f}%.",
    ]
    if anomalies:
        lines.append(f"  {len(anomalies)} voltage anomaly event(s) detected.")
    lines.append(f"  Assessment: {mech}")
    return "\n".join(lines)
