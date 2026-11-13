"""
Diffusion coefficients from galvanostatic titration (GITT), two ways:

  gitt (Weppner–Huggins)   from each titration step and the relaxation after it
        D = 4/(pi tau) * L^2 * (dEs / dEt)^2
        tau = titration time, L = diffusion length (r/3 for a sphere, or the
        film thickness), dEs = change of the relaxed OCV from the previous state,
        dEt = potential change during the titration (first sample -> last,
        so the instant IR jump is excluded). Valid while tau << L^2/D
        (reported as tau*D/L^2).

  sqrt_t (the workbook method)   from one short pulse per state
        D = 4/pi * (I V_M / (F S))^2 * ((dE/dx) / (dE/dsqrt t))^2
        dE/dsqrt t = slope of E vs sqrt(t) over the pulse, dE/dx = slope of the
        OCV vs composition x, V_M = molar volume M(x)/density, S = area.
        dEdx: central (default; centred difference over the neighbouring states) or
        excel (backward difference over two states, as in the hand-built workbook).

Composition (needed for sqrt_t), in gitt.yaml per sample:
    composition:
      formula: {Li: x, C: 8, H: 3, N: 1, S: 2}     # 'x' is the varying amount
      x_start: 0.2                                  # x at state 0
      x_end: 1.0                                    # x at state 100 (%)
    density_g_cm3: 1.7                              # or taken from particle:
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from battery_analysis.analysis.pulse import read_pulse_file, pulse_files, analyse_state, particle_geometry, molar_mass
from battery_analysis.analysis.pulse.geometry import FARADAY
from battery_analysis.analysis.pulse.config import state_labels


@dataclass
class GITTSample:
    sample: str
    geometry: dict
    table: pd.DataFrame                    # one row per state
    traces: dict = field(default_factory=dict, repr=False)   # state -> (t, E) of the sqrt(t) pulse
    settings: dict = field(default_factory=dict, repr=False)


def _sqrt_t_slope(trace: pd.DataFrame):
    x = np.sqrt(trace["t_s"].to_numpy(float))
    y = trace["E_V"].to_numpy(float)
    if len(x) < 3:
        return np.nan, np.nan
    k, c = np.polyfit(x, y, 1)
    r2 = 1 - np.sum((y - (k * x + c)) ** 2) / np.sum((y - y.mean()) ** 2) if np.ptp(y) > 0 else np.nan
    return float(k), float(r2)


def _pick_sqrt_t_pulse(state, wanted_A=None, min_r2=0.99):
    """The charge pulse used for sqrt(t): the given current, else the largest Tafel pulse with E ~ sqrt(t) linear."""
    p = state.tafel_pulses()
    p = p[p["I_A"] > 0]
    if p.empty:
        return None, np.nan, np.nan
    if wanted_A:
        row = p.iloc[(p["I_A"] - float(wanted_A)).abs().argmin()]
        k, r2 = _sqrt_t_slope(state.pulse_trace(row))
        return row, k, r2
    best = None
    for _, row in p.sort_values("I_A", ascending=False).iterrows():
        k, r2 = _sqrt_t_slope(state.pulse_trace(row))
        if np.isfinite(r2) and r2 >= min_r2:
            return row, k, r2
        if best is None or (np.isfinite(r2) and r2 > best[2]):
            best = (row, k, r2)
    return best if best else (None, np.nan, np.nan)


def analyse_sample(sample: str, spec: dict, verbose: bool = True) -> GITTSample:
    geom = particle_geometry(spec)
    L = geom["diffusion_length_cm"]
    S = geom["area_cm2"]
    files = pulse_files(spec["folder"], spec.get("file_pattern", "*.txt"))
    states, titr_carry = [], None
    for f in files:
        st = analyse_state(read_pulse_file(f), zero_current=spec.get("zero_current_A"),
                           pulse_max_s=float(spec.get("pulse_max_s", 30)),
                           ocv_rest_min_s=float(spec.get("ocv_rest_min_s", 300)),
                           tafel_pulse_s=spec.get("tafel_pulse_s", "auto"))
        if not st.is_pulse_file:
            # a titration recorded in its own file (coin-cell layout): use it for the next state
            cur = st.steps[st.steps["kind"] == "current"]
            if not cur.empty:
                r = cur.loc[cur["duration_s"].idxmax()]
                rest_after = st.steps[(st.steps["kind"] == "rest") & (st.steps.index > cur["duration_s"].idxmax())]
                titr_carry = {"I_A": float(r["I_A"]), "duration_s": float(r["duration_s"]),
                              "E_first_V": float(r["E_first_V"]), "E_last_V": float(r["E_last_V"]),
                              "E_before_V": float(r["E_before_V"]),
                              "ocv_after_V": float(rest_after.iloc[-1]["E_last_V"]) if not rest_after.empty else np.nan}
            continue
        if st.titration is None and titr_carry is not None:
            st.titration = titr_carry
        titr_carry = None
        states.append(st)
    only = spec.get("files")
    if only:
        states = [s for s in states if Path(s.source).name in set(only)]
    labels = state_labels(spec, len(states))

    comp = spec.get("composition") or {}
    density = spec.get("density_g_cm3") or (spec.get("particle") or {}).get("density_g_cm3")
    x0, x1 = float(comp.get("x_start", 0.0)), float(comp.get("x_end", 1.0))
    xs = [x0 + (float(l) / 100.0) * (x1 - x0) if isinstance(l, (int, float)) else np.nan for l in labels]
    ocvs = [s.ocv_V for s in states]
    initial = spec.get("initial_ocv_V", states[0].initial_ocv_V if states else np.nan)

    def VM(x):
        if not comp.get("formula") or not density or not np.isfinite(x):
            return np.nan
        f = {el: (x if str(n).strip() == "x" else float(n)) for el, n in comp["formula"].items()}
        return molar_mass(f) / float(density)                       # cm3/mol

    mode = spec.get("dEdx", "central")
    sqrt_currents = spec.get("sqrt_t_current_A") or {}
    rows, traces = [], {}
    for k, (lab, st, x) in enumerate(zip(labels, states, xs)):
        row = {"sample": sample, "state": lab, "file": Path(st.source).name, "x": x, "OCV_V": st.ocv_V}
        # --- classic GITT
        tit = st.titration
        prev = ocvs[k - 1] if k > 0 else (tit.get("E_before_V") if tit else np.nan)
        if tit:
            tau = tit["duration_s"]
            dEt = tit["E_last_V"] - tit["E_first_V"]
            dEs = st.ocv_V - prev if np.isfinite(prev) else np.nan
            D = 4 / (math.pi * tau) * L ** 2 * (dEs / dEt) ** 2 if (np.isfinite(L) and dEt and np.isfinite(dEs)) else np.nan
            row.update(titration_I_A=tit["I_A"], tau_s=tau, dEs_V=dEs, dEt_V=dEt, D_gitt_cm2_s=D,
                       gitt_validity_tauD_L2=tau * D / L ** 2 if np.isfinite(D) and np.isfinite(L) else np.nan)
        # --- sqrt(t)
        want = sqrt_currents.get(lab) if isinstance(sqrt_currents, dict) else sqrt_currents
        prow, slope, r2 = _pick_sqrt_t_pulse(st, want, float(spec.get("sqrt_t_min_r2", 0.99)))
        if mode == "excel":
            if k >= 2:
                dEdx = (ocvs[k] - ocvs[k - 2]) / (xs[k] - xs[k - 2])
            else:
                x_prev = x0 if k == 0 else xs[k - 1]
                o_prev = initial if k == 0 else ocvs[k - 1]
                dEdx = (ocvs[k] - o_prev) / (x - x_prev) if np.isfinite(o_prev) else np.nan
        else:
            dEdx = float(np.gradient(ocvs, xs)[k]) if len(states) > 1 and np.all(np.isfinite(xs)) else np.nan
        Vm = VM(x)
        if prow is not None and np.isfinite(slope) and slope != 0:
            I = float(prow["I_A"])
            Dq = 4 / math.pi * (I * Vm / (FARADAY * S)) ** 2 * (dEdx / slope) ** 2 if np.isfinite(Vm) else np.nan
            row.update(sqrt_t_I_A=I, dE_dsqrt_t=slope, sqrt_t_r2=r2, dE_dx=dEdx, Vm_cm3_mol=Vm, D_sqrt_t_cm2_s=Dq)
            traces[lab] = st.pulse_trace(prow)
        rows.append(row)
    if verbose:
        print(f"  {sample}: {len(rows)} states (diffusion length {L * 1e4:.3g} µm, area {S:.3e} cm²)")
    return GITTSample(sample=sample, geometry=geom, table=pd.DataFrame(rows), traces=traces, settings=spec)


def read_reference_workbook(path) -> pd.DataFrame:
    """The workbook's sqrt(t) diffusivities: 'Overpotential' sheet, Diffusivity (col R) and DOD (col S)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    if "Overpotential" not in wb.sheetnames:
        return pd.DataFrame()
    ws = wb["Overpotential"]
    rows = []
    for r in range(29, 60):
        d, s = ws.cell(r, 18).value, ws.cell(r, 19).value
        if isinstance(d, (int, float)) and isinstance(s, (int, float)):
            rows.append({"state": int(s), "excel_D_cm2_s": float(d),
                         "excel_dE_dx": ws.cell(r, 13).value, "excel_dE_dsqrt_t": ws.cell(r, 14).value})
    wb.close()
    return pd.DataFrame(rows)
