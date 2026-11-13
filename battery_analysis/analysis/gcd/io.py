"""
Raw data loader for Zive/Wonatech ASCII exports (Li-Li symmetric-cell
plating/stripping tests). Ported verbatim from _parse_zive_time /
_read_zive_csv in the original battery_analysis/gcd.py — this is also
exactly what ce.py used (see analysis/gcd/README note in the package
docstring: ce.py turned out to be gcd.py plus one extra calculation).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


def parse_zive_time(time_str: str):
    """Convert Zive time string d:hh:mm:ss.ms or hh:mm:ss.ms to hours."""
    try:
        parts = str(time_str).strip().split(":")
        if len(parts) == 4:
            d, h, m, s = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
        elif len(parts) == 3:
            d, h, m, s = 0, float(parts[0]), float(parts[1]), float(parts[2])
        else:
            return None
        return d * 24 + h + m / 60 + s / 3600
    except Exception:
        return None


def read_zive_csv(filepath, cutoff_v: float = 1.0, rest_hours: float = 10.0):
    """
    Read one Zive CSV/ASCII export file. Returns a DataFrame with columns
    [Time(h), Voltage(V)] starting at t=0, truncated the moment
    |Voltage| >= cutoff_v. rest_hours: OCV rest time skipped at the start.
    Returns None if the file can't be parsed.
    """
    if not os.path.isfile(filepath):
        return None

    with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
        lines = fh.readlines()

    header_idx = next(
        (i for i, ln in enumerate(lines) if "Test Time" in ln and "Voltage" in ln), None
    )
    if header_idx is None:
        return None

    sep = "\t" if "\t" in lines[header_idx] else ","

    df = pd.read_csv(
        filepath, skiprows=header_idx, sep=sep, engine="python",
        encoding="utf-8", encoding_errors="ignore",
    )
    df.columns = [c.strip() for c in df.columns]

    time_col = next((c for c in df.columns if "Test Time" in c), None)
    voltage_col = next((c for c in df.columns if "Voltage" in c), None)
    if time_col is None or voltage_col is None:
        return None

    df = df[[time_col, voltage_col]].copy()
    df.columns = ["_t_raw", "Voltage(V)"]
    df["Time(h)"] = df["_t_raw"].apply(parse_zive_time)
    df["Voltage(V)"] = pd.to_numeric(df["Voltage(V)"], errors="coerce")
    df = df.dropna(subset=["Time(h)", "Voltage(V)"])

    df = df[df["Time(h)"] >= rest_hours]
    df["Time(h)"] = df["Time(h)"] - df["Time(h)"].min()

    hit = df.index[df["Voltage(V)"].abs() >= cutoff_v].tolist()
    if hit:
        df = df.loc[: hit[0] - 1]

    if df.empty:
        return None

    return df[["Time(h)", "Voltage(V)"]].reset_index(drop=True)


def is_zive_csv(filepath) -> bool:
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4000)
        return "Test Time" in head and "Voltage" in head
    except Exception:
        return False


# =========================================================
# NEWARE (Na‖Al / Na‖Cu plating/stripping)
# =========================================================

def is_neware_csv(filepath) -> bool:
    """A BTSDA CSV export has a 'Cycle Index' / 'Step Type' header, a Zive one doesn't."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4000)
        return "Cycle Index" in head and "Step Type" in head
    except Exception:
        return False


def read_neware_gcd(filepath, cutoff_v: float = 1.0, plating_sign: int = -1):
    """
    Read one NEWARE file (BTSDA .xlsx/.csv, or .ndax) into the same table
    read_zive_csv() returns — [Time(h), Voltage(V)] from t = 0 — plus a
    `phase` column ("plating" / "stripping") taken straight from the step
    type (CC DChg / CC Chg), so analyze_gcd() doesn't have to guess the
    half-cycles from the voltage sign. Rest steps are dropped (the opening
    rest replaces rest_hours).

    cutoff_v here is the STRIPPING cut-off of the NEWARE step plan: a last
    cycle whose stripping never reached it (test still running when the
    file was exported) is left out, with a printed note.
    Returns None if the file can't be read.
    """
    from battery_analysis.analysis.cycling_common.neware_io import NewareParser
    try:
        rec = NewareParser(filepath).records
    except Exception as e:
        print(f"⚠️  {Path(filepath).name}: not a readable NEWARE file ({type(e).__name__}: {e})")
        return None
    rec = rec[rec["direction"] != "rest"]
    if rec.empty or "time_s" not in rec:
        return None
    plating = "discharge" if plating_sign == -1 else "charge"
    phase = (rec["direction"] == plating).map({True: "plating", False: "stripping"})

    # leave out a last cycle whose stripping hadn't finished yet
    last = rec["cycle"].max()
    strip_last = rec[(rec["cycle"] == last) & (phase == "stripping")]
    if strip_last.empty or strip_last["voltage_V"].abs().max() < cutoff_v - 0.05:
        print(f"   {Path(filepath).name}: cycle {last} left out — stripping didn't reach "
              f"{cutoff_v:g} V yet (test still running?)")
        keep = rec["cycle"] < last
        rec, phase = rec[keep], phase[keep]
    # Every recorded point is kept, including the cut-off point at the end of
    # stripping (the jump to ~1 V). Dropping it made every stripping curve end
    # at whatever the point before the jump happened to be (0.1-0.9 V) instead
    # of at the 1 V cut-off, and also hid plating excursions below -0.95 V.
    # analyze_gcd() leaves the jump out of the stripping-plateau and spike
    # metrics itself (cutoff_v).
    if rec.empty:
        return None

    # capacity the cycler measured within each half-cycle (mAh), steps chained:
    # NEWARE resets capacity at every step
    cap = np.where(phase == "plating", rec["discharge_capacity_mAh"], rec["charge_capacity_mAh"]).astype(float)
    half = ((phase != phase.shift()) | (rec["cycle"] != rec["cycle"].shift())).cumsum().to_numpy()
    step = rec["step"].to_numpy()
    cap_df = pd.DataFrame({"half": half, "step": step, "cap": cap})
    step_end = cap_df.groupby(["half", "step"], sort=False)["cap"].max()
    offset = step_end.groupby(level="half").cumsum() - step_end
    cap = cap + offset.reindex(pd.MultiIndex.from_arrays([half, step])).to_numpy()

    t = rec["time_s"].to_numpy(float) / 3600
    return pd.DataFrame({"Time(h)": t - t[0], "Voltage(V)": rec["voltage_V"].to_numpy(float),
                         "phase": phase.to_numpy(), "Capacity(mAh)": cap,
                         "Current(mA)": rec["current_mA"].to_numpy(float)})
