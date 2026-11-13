"""
Splitting a galvanostatic record into constant-current steps and rests, and
reading one state of charge out of it (OCV, titration step, pulses).

Nothing about a particular protocol is hard-coded: the rest level, the pulse
length and the titration step are found from the data; every threshold can be
overridden from the YAML.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd


def zero_current_level(I: np.ndarray) -> float:
    """
    Current below which a point counts as a rest. Rests dominate a pulse
    record in time, so the 20th percentile of |I| is the rest/offset level;
    anything 5x above it is a real current step.
    """
    a = np.abs(np.asarray(I, float))
    a = a[np.isfinite(a)]
    if a.size == 0:
        return 0.0
    base = float(np.percentile(a, 20))
    floor = 1e-6 * float(a.max()) if a.max() > 0 else 0.0
    return max(5.0 * base, floor, 1e-15)


def segment(df: pd.DataFrame, zero_current: Optional[float] = None, rel_jump: float = 0.1) -> pd.DataFrame:
    """
    One row per step: kind (rest|current), first/last row, start time,
    duration, median current, first/last potential, potential just before.
    A new step starts when the cell switches between rest and current, or when
    the current jumps by more than rel_jump (10 %) of its size.
    """
    t, I, E = df["t_s"].to_numpy(float), df["I_A"].to_numpy(float), df["E_V"].to_numpy(float)
    tol = zero_current_level(I) if zero_current is None else float(zero_current)
    active = np.abs(I) >= tol
    new = np.zeros(len(I), bool)
    new[0] = True
    jump = np.abs(np.diff(I)) > np.maximum(tol, rel_jump * np.maximum(np.abs(I[1:]), np.abs(I[:-1])))
    new[1:] = (active[1:] != active[:-1]) | (active[1:] & active[:-1] & jump)
    seg_id = np.cumsum(new) - 1
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1.0

    rows = []
    for k in range(seg_id[-1] + 1 if len(seg_id) else 0):
        idx = np.flatnonzero(seg_id == k)
        i0, i1 = int(idx[0]), int(idx[-1])
        rows.append({
            "kind": "current" if active[i0] else "rest",
            "i0": i0, "i1": i1,
            "t_start_s": t[i0],
            "duration_s": t[i1] - t[i0] + dt,
            "I_A": float(np.median(I[i0:i1 + 1])),
            "E_first_V": E[i0], "E_last_V": E[i1],
            "E_before_V": E[i0 - 1] if i0 > 0 else np.nan,
        })
    out = pd.DataFrame(rows)
    out.attrs["zero_current_A"] = tol
    out.attrs["dt_s"] = dt
    return out


@dataclass
class PulseState:
    """What one pulse file (one state of charge) contains."""
    source: str
    ocv_V: float                          # end of the relaxation rest before the pulses
    ocv_rest_s: float
    titration: Optional[dict]             # the step that set this state: I_A, duration_s, E_first_V, E_last_V
    initial_ocv_V: float                  # last rest before the titration (previous state's OCV, if recorded)
    pulses: pd.DataFrame                  # one row per short pulse
    steps: pd.DataFrame = field(repr=False)
    data: pd.DataFrame = field(repr=False)
    tafel_pulse_s: float = np.nan         # the pulse length used for Tafel points

    @property
    def is_pulse_file(self) -> bool:
        return len(self.pulses) >= 4

    def tafel_pulses(self) -> pd.DataFrame:
        p = self.pulses
        if p.empty or not np.isfinite(self.tafel_pulse_s):
            return p.iloc[0:0]
        dt = self.steps.attrs.get("dt_s", 1.0)
        return p[np.abs(p["duration_s"] - self.tafel_pulse_s) <= 0.5 * dt + 1e-9]

    def pulse_trace(self, row) -> pd.DataFrame:
        """t (s from pulse start), E (V) of one pulse row."""
        d = self.data.iloc[int(row["i0"]):int(row["i1"]) + 1]
        t0 = self.data["t_s"].iloc[int(row["i0"])] - self.steps.attrs.get("dt_s", 1.0)
        return pd.DataFrame({"t_s": d["t_s"].to_numpy() - t0, "E_V": d["E_V"].to_numpy()})


def analyse_state(df: pd.DataFrame, zero_current: Optional[float] = None, pulse_max_s: float = 30.0,
                  ocv_rest_min_s: float = 300.0, tafel_pulse_s="auto", titration_min_s=None) -> PulseState:
    """
    Read one state out of a pulse file:
      relaxation = the rest (>= ocv_rest_min_s) right before the first short pulse;
                   its last potential is the OCV
      titration  = the current step right before that rest (any length), if there is one
      pulses     = every current step <= pulse_max_s after the relaxation, with the
                   potential just before it (E_before), at its start and at its end
      Tafel pulse length = the most common pulse length (auto) or the given value
    (titration_min_s is accepted for older YAMLs and ignored.)
    """
    st = segment(df, zero_current)
    kinds, durs = st["kind"].to_numpy(), st["duration_s"].to_numpy()
    relax_idx = None
    for k in range(1, len(st)):
        if kinds[k] == "current" and durs[k] <= pulse_max_s and kinds[k - 1] == "rest" and durs[k - 1] >= ocv_rest_min_s:
            relax_idx = k - 1
            break

    if relax_idx is not None:
        rest = st.loc[relax_idx]
        ocv, ocv_rest = float(rest["E_last_V"]), float(rest["duration_s"])
        start = relax_idx + 1
    else:                                   # no relaxation recorded: potential before the first pulse
        first = next((k for k in range(len(st)) if kinds[k] == "current" and durs[k] <= pulse_max_s), None)
        ocv = float(st.loc[first, "E_before_V"]) if first is not None and np.isfinite(st.loc[first, "E_before_V"]) \
            else float(df["E_V"].iloc[0])
        ocv_rest, start = 0.0, (first if first is not None else len(st))

    titration = None
    if relax_idx is not None and relax_idx >= 1 and kinds[relax_idx - 1] == "current":
        r = st.loc[relax_idx - 1]
        titration = {k: float(r[k]) for k in ("I_A", "duration_s", "E_first_V", "E_last_V", "E_before_V", "t_start_s")}
        titration.update(i0=int(r["i0"]), i1=int(r["i1"]))
        before = st[(st["kind"] == "rest") & (st.index < relax_idx - 1)]
    else:
        before = st[(st["kind"] == "rest") & (st.index < (relax_idx if relax_idx is not None else 0))]
    initial = float(before.iloc[-1]["E_last_V"]) if not before.empty else np.nan

    after = st.iloc[start:]
    pulses = after[(after["kind"] == "current") & (after["duration_s"] <= pulse_max_s)].copy()
    pulses["order"] = range(len(pulses))
    if tafel_pulse_s == "auto":
        dur = np.round(pulses["duration_s"]).astype(int)
        tps = float(dur.mode().min()) if not dur.empty else np.nan
    else:
        tps = float(tafel_pulse_s)
    return PulseState(source=df.attrs.get("source", ""), ocv_V=ocv, ocv_rest_s=ocv_rest, titration=titration,
                      initial_ocv_V=initial, pulses=pulses.reset_index(drop=True), steps=st, data=df,
                      tafel_pulse_s=tps)
