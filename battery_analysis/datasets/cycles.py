"""
Time series -> one row per cycle, the same way for every cycler.

Capacities are integrated from current × time, not taken from the cycler's
capacity columns: those reset per step on some cyclers (NEWARE, Arbin),
per cycle on others, or are missing (BDF only requires t, V, I). Integration
is identical for all of them. tests check it against the cycler's own values.

A cycle is cycle_count when the file has it. Otherwise a new cycle starts at
the first charge after a discharge.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

I_EPS = 1e-6   # A; |I| below this counts as rest


def cycle_index(ts: pd.DataFrame) -> np.ndarray:
    if "cycle_count" in ts and ts["cycle_count"].notna().any() and ts["cycle_count"].nunique() > 1:
        return ts["cycle_count"].ffill().fillna(0).to_numpy(dtype=np.int64)
    i = ts["current_ampere"].to_numpy()
    sign = np.where(i > I_EPS, 1, np.where(i < -I_EPS, -1, 0))
    nz = sign != 0
    last = pd.Series(np.where(nz, sign, np.nan)).ffill().to_numpy()
    prev = np.r_[np.nan, last[:-1]]
    starts = (last == 1) & (prev == -1)
    return 1 + np.cumsum(starts)


def summarize(ts: pd.DataFrame) -> pd.DataFrame:
    """
    Columns: cycle, charge_ah, discharge_ah, ce (discharge/charge), charge_wh,
    discharge_wh, mean_charge_v, mean_discharge_v, v_max, v_min, duration_h,
    t_start_h, temperature_c, n_points.
    """
    t = ts["test_time_second"].to_numpy(dtype=float)
    v = ts["voltage_volt"].to_numpy(dtype=float)
    i = ts["current_ampere"].to_numpy(dtype=float)
    dt = np.r_[0.0, np.diff(t)]
    dt[(dt < 0) | (dt > 3600 * 6)] = 0.0             # time resets / logging gaps carry no charge
    # trapezoid inside a step; across a sign change use the newer point (the new step's current)
    i_prev = np.r_[i[0], i[:-1]]
    same = np.sign(i_prev) == np.sign(i)
    i_mid = np.where(same, 0.5 * (i + i_prev), i)
    v_prev = np.r_[v[0], v[:-1]]
    dq = i_mid * dt / 3600.0                          # Ah
    de = dq * 0.5 * (v + v_prev)                      # Wh
    temp_col = next((c for c in ("temperature_t1_celsius", "ambient_temperature_celsius") if c in ts), None)

    df = pd.DataFrame({
        "cycle": cycle_index(ts), "dq": dq, "de": de, "t": t, "v": v,
        "temp": ts[temp_col].to_numpy(dtype=float) if temp_col else np.nan,
    })
    df["dq_ch"] = df["dq"].clip(lower=0)
    df["dq_dis"] = (-df["dq"]).clip(lower=0)
    df["de_ch"] = df["de"].where(df["dq"] > 0, 0.0)
    df["de_dis"] = (-df["de"]).where(df["dq"] < 0, 0.0)
    g = df.groupby("cycle", sort=True)
    out = pd.DataFrame({
        "charge_ah": g["dq_ch"].sum(), "discharge_ah": g["dq_dis"].sum(),
        "charge_wh": g["de_ch"].sum(), "discharge_wh": g["de_dis"].sum(),
        "v_max": g["v"].max(), "v_min": g["v"].min(),
        "t_start_h": g["t"].min() / 3600, "duration_h": (g["t"].max() - g["t"].min()) / 3600,
        "temperature_c": g["temp"].mean(), "n_points": g.size(),
    }).reset_index()
    with np.errstate(divide="ignore", invalid="ignore"):
        out["ce"] = np.where(out["charge_ah"] > 0, out["discharge_ah"] / out["charge_ah"], np.nan)
        out["mean_charge_v"] = np.where(out["charge_ah"] > 0, out["charge_wh"] / out["charge_ah"], np.nan)
        out["mean_discharge_v"] = np.where(out["discharge_ah"] > 0, out["discharge_wh"] / out["discharge_ah"], np.nan)
    cols = ["cycle", "charge_ah", "discharge_ah", "ce", "charge_wh", "discharge_wh", "mean_charge_v",
            "mean_discharge_v", "v_max", "v_min", "t_start_h", "duration_h", "temperature_c", "n_points"]
    return out[cols]
