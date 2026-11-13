"""
One column standard for every time series: the Battery Data Format (BDF).

BDF keys carry their unit (voltage_volt, test_time_second). A file may use
another unit for the same quantity (test_time_millisecond, current_milliampere):
normalize() converts those to the canonical key. Unknown columns are kept.

Required: test_time_second, voltage_volt, current_ampere.
Current sign: positive = charge, negative = discharge (BDF convention).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

REQUIRED = ["test_time_second", "voltage_volt", "current_ampere"]
OPTIONAL = [
    "unix_time_second", "cycle_count", "step_count", "step_id", "step_type",
    "step_time_second", "ambient_temperature_celsius", "temperature_t1_celsius",
    "charging_capacity_ampere_hour", "discharging_capacity_ampere_hour",
]
EIS_COLUMNS = ["frequency_hertz", "z_real_ohm", "z_imag_ohm"]   # z_imag as measured (negative = capacitive)

# unit word -> (canonical unit word, factor to multiply by)
_UNITS = {
    "millisecond": ("second", 1e-3), "minute": ("second", 60.0), "hour": ("second", 3600.0),
    "millivolt": ("volt", 1e-3),
    "milliampere": ("ampere", 1e-3), "microampere": ("ampere", 1e-6),
    "milliampere_hour": ("ampere_hour", 1e-3), "mah": ("ampere_hour", 1e-3),
    "milliohm": ("ohm", 1e-3),
}
# whole-key renames (BDF variants seen in real files)
_ALIASES = {
    "date_time_millisecond": ("unix_time_second", 1e-3),
    "date_time_second": ("unix_time_second", 1.0),
    "cycle_dimensionless": ("cycle_count", 1),
    "step_dimensionless": ("step_count", 1),
}
SENTINELS = (-9999.0, -999.0, 9.9e37)      # "no value" markers some cyclers write


def canonical_name(col: str) -> tuple[str, float]:
    """'test_time_millisecond' -> ('test_time_second', 0.001)."""
    if col in _ALIASES:
        return _ALIASES[col]
    for unit in sorted(_UNITS, key=len, reverse=True):
        if col.endswith("_" + unit):
            canon, factor = _UNITS[unit]
            return col[: -len(unit)] + canon, factor
    return col, 1.0


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Rename and rescale to canonical BDF keys; numbers to float64/int64."""
    out = {}
    for col in df.columns:
        key = re.sub(r"\W+", "_", str(col).strip().lower()).strip("_")
        name, factor = canonical_name(key)
        s = df[col]
        if factor != 1 and pd.api.types.is_numeric_dtype(s):
            s = s.astype("float64") * factor
        if name in out:              # two source columns for one quantity: keep the first
            continue
        out[name] = s
    df = pd.DataFrame(out)
    for c in ("cycle_count", "step_count", "step_id"):
        if c in df and pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].astype("Int64")
    return df


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Fix what can be fixed safely, report everything:
    sentinel values -> NaN, rows without time/voltage/current dropped,
    duplicated or backwards time stamps flagged (time is NOT re-sorted:
    the row order is the order the cycler wrote).
    """
    issues = []
    missing = [c for c in REQUIRED if c not in df]
    if missing:
        raise ValueError(f"missing required column(s) {missing}; have {list(df.columns)}")
    num = df.select_dtypes("number").columns
    for c in num:
        bad = df[c].isin(SENTINELS)
        if bad.any():
            issues.append(f"{c}: {int(bad.sum())} sentinel value(s) set to NaN")
            df.loc[bad, c] = np.nan
    if all(c in df for c in num) and len(num):
        for c in [c for c in num if df[c].isna().all()]:
            df = df.drop(columns=c)
            issues.append(f"{c}: no valid values, column dropped")
    n0 = len(df)
    df = df.dropna(subset=REQUIRED).reset_index(drop=True)
    if len(df) < n0:
        issues.append(f"{n0 - len(df)} row(s) without time/voltage/current dropped")
    df, note = fix_unsigned_current(df)
    if note:
        issues.append(note)
    t = df["test_time_second"].to_numpy()
    back = int((np.diff(t) < 0).sum())
    if back:
        issues.append(f"test_time_second goes backwards {back} time(s) — check the export")
    v = df["voltage_volt"].to_numpy()
    if len(v) and (np.nanmax(v) > 10 or np.nanmin(v) < -10):
        issues.append(f"voltage outside ±10 V (min {np.nanmin(v):.3g}, max {np.nanmax(v):.3g}) — unit or outlier problem")
    return df, issues


def fix_unsigned_current(df: pd.DataFrame, i_eps: float = 1e-6, dv_flat: float = 0.005):
    """
    Some exports store |I| only (seen in a real Maccor BDF file): every step
    then looks like a charge. Detect it (no negative current at all, yet the
    voltage falls during current-on steps) and give each step the sign of its
    voltage change. A step whose voltage hardly moves (a CV hold) takes the
    sign of the step before it. Returns (df, note) — note is "" when nothing changed.
    """
    i = df["current_ampere"].to_numpy(dtype=float)
    if (i < -i_eps).any() or not (i > i_eps).any():
        return df, ""
    if "step_count" in df and df["step_count"].notna().all():
        step = df["step_count"].to_numpy()
    else:
        on = i > i_eps
        cut = (np.diff(on.astype(int)) != 0) | (np.abs(np.diff(i)) > 0.2 * np.abs(i[1:]) + i_eps)
        if "cycle_count" in df:                       # a new cycle is always a new step
            cut |= np.diff(df["cycle_count"].fillna(-1).to_numpy(dtype=float)) != 0
        step = np.cumsum(np.r_[True, cut])
    v = df["voltage_volt"].to_numpy(dtype=float)
    g = pd.DataFrame({"step": step, "v": v, "on": i > i_eps}).groupby("step", sort=False)
    dv = g["v"].last() - g["v"].first()
    active = g["on"].mean() > 0.5
    signs, prev, falling = {}, 1.0, 0
    for s in dv.index:
        if not active[s]:
            signs[s] = 1.0
            continue
        if abs(dv[s]) > dv_flat:
            prev = 1.0 if dv[s] > 0 else -1.0
            falling += prev < 0
        signs[s] = prev
    if falling == 0:
        return df, ""                  # voltage never falls under current: really charge-only data
    df = df.copy()
    df["current_ampere"] = i * pd.Series(step).map(signs).to_numpy()
    return df, (f"current_ampere had no sign (|I| only); sign set per step from the voltage direction "
                f"({falling} discharge step(s)) — check against the original export")


def validate_eis(df: pd.DataFrame) -> list[str]:
    missing = [c for c in EIS_COLUMNS if c not in df]
    return [f"EIS: missing {missing}"] if missing else []
