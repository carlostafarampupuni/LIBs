"""
Charge-transfer kinetics from galvanostatic pulses (Tafel / Butler–Volmer).

For every state of charge (one pulse file) the Tafel points are the potential
at the END of each short pulse (the same reading as the hand-built Excel
workbooks), against log10 of the current density.

Four ways to get the exchange current density i0 from those points, reported
side by side so they can be compared:

  excel           The workbook method, done objectively: transfer coefficient fixed
                  (alpha = 0.5), both lines forced through the OCV, and the common
                  intercept log10(i0) found by least squares instead of by eye.
                  log|i| = log i0 + alpha F/(2.303 R T) |eta|
  butler_volmer   Full Butler–Volmer equation fitted to ALL points (low and high
                  overpotential): i = i0 [exp(aa F eta/RT) - exp(-ac F eta/RT)],
                  with i0, aa and ac free and standard errors reported.
  tafel_region    Straight-line fits of log|i| vs eta using only |eta| >= eta_min
                  (default 50 mV), i.e. only where the Tafel approximation holds;
                  slope -> alpha, intercept -> i0 (one per branch, then the mean).
  linear          Micro-polarisation: points with |eta| <= eta_max (default 15 mV),
                  i = i0 F eta / (R T) -> the charge-transfer resistance directly.

Rct = R T / (F i0) for every method (ohm cm2).

Overpotential: `ocv` (E_end - OCV, as in the workbooks) or `local`
(E_end - E just before the pulse) — the second removes the drift left when the
rest between pulses is too short to return to the OCV.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from battery_analysis.analysis.pulse import read_pulse_file, pulse_files, analyse_state, particle_geometry
from battery_analysis.analysis.pulse.geometry import FARADAY, R_GAS, tafel_slope_per_V
from battery_analysis.analysis.pulse.config import state_labels

LN10 = math.log(10)
METHODS = ["tafel", "excel", "butler_volmer", "tafel_region", "linear"]


# ──────────────────────────────────────────────────────────────────────
# Points
# ──────────────────────────────────────────────────────────────────────
def series_resistance(state) -> float:
    """
    Ohmic-like resistance from the instant jump at the start of each Tafel pulse
    (first sample minus the potential just before), fitted against current through
    the origin. The first sample also holds part of the charge-transfer response,
    so this is an upper estimate; an EIS high-frequency resistance is better if you have it.
    """
    p = state.tafel_pulses()
    if len(p) < 3:
        return np.nan
    I = p["I_A"].to_numpy(float)
    jump = (p["E_first_V"] - p["E_before_V"]).to_numpy(float)
    ok = np.isfinite(jump)
    return float((I[ok] * jump[ok]).sum() / (I[ok] ** 2).sum()) if ok.sum() >= 3 else np.nan


def tafel_points(state, area_cm2: float, overpotential: str = "ocv", repeat: str = "last",
                 current_range_A=None, currents_A=None, ir_correction=None) -> pd.DataFrame:
    """
    One row per Tafel pulse kept: branch, I_A, i_A_cm2, log_i, E_V, E_before_V, eta_V.
    currents_A: optional list of nominal currents to use (each matched within 15 %);
    other pulse currents are left out (e.g. the 1.5 nA step the workbooks skip).
    ir_correction: None/"none" (as in the workbooks), "auto" (series_resistance()) or a
    resistance in ohm; eta is then E_end - reference - I*R.
    """
    p = state.tafel_pulses().copy()
    if p.empty:
        return pd.DataFrame(columns=["branch", "I_A", "i_A_cm2", "log_i", "E_V", "E_before_V", "eta_V"])
    p["branch"] = np.where(p["I_A"] > 0, "charge", "discharge")
    p["Imag"] = p["I_A"].abs()
    lo, hi = (current_range_A or (None, None)) if isinstance(current_range_A, (list, tuple)) else (None, None)
    if lo is not None:
        p = p[p["Imag"] >= float(lo) * 0.98]
    if hi is not None:
        p = p[p["Imag"] <= float(hi) * 1.02]
    if currents_A:
        nominal = np.abs(np.asarray(currents_A, float))
        near = p["Imag"].map(lambda v: np.min(np.abs(nominal - v) / nominal))
        p = p[near <= 0.15]
    # same current applied more than once (e.g. the +/-1 nA probes): keep one
    key = p["Imag"].map(lambda v: float(f"{v:.2g}"))
    p = p.assign(_k=key).sort_values("order")
    p = p.groupby(["branch", "_k"], as_index=False).nth(-1 if repeat == "last" else 0)
    E = p["E_last_V"].to_numpy(float)
    ref = state.ocv_V if overpotential == "ocv" else p["E_before_V"].to_numpy(float)
    out = pd.DataFrame({
        "branch": p["branch"].to_numpy(),
        "I_A": p["I_A"].to_numpy(float),
        "i_A_cm2": p["I_A"].to_numpy(float) / area_cm2,
        "E_V": E,
        "E_before_V": p["E_before_V"].to_numpy(float),
        "eta_V": E - ref,
        "pulse_s": p["duration_s"].to_numpy(float),
    })
    R = 0.0
    if ir_correction not in (None, "none", False, 0):
        R = series_resistance(state) if ir_correction == "auto" else float(ir_correction)
        if np.isfinite(R):
            out["eta_V"] = out["eta_V"] - out["I_A"] * R
    out["R_ir_ohm"] = R if np.isfinite(R) else np.nan
    out["log_i"] = np.log10(np.abs(out["i_A_cm2"]))
    return out.sort_values(["branch", "log_i"]).reset_index(drop=True)


def _consistent(pts: pd.DataFrame) -> pd.DataFrame:
    """Points whose overpotential has the same sign as their current (physically usable)."""
    return pts[np.sign(pts["eta_V"]) == np.sign(pts["I_A"])]


# ──────────────────────────────────────────────────────────────────────
# Fits
# ──────────────────────────────────────────────────────────────────────
def _rct(i0, T):
    return R_GAS * T / (FARADAY * i0) if i0 and np.isfinite(i0) and i0 > 0 else np.nan


def fit_excel(pts, T, alpha=0.5, alpha_c=None) -> dict:
    """Workbook method with an objective intercept (least squares, both branches, lines through the OCV)."""
    p = _consistent(pts)
    if len(p) < 2:
        return {"method": "excel", "ok": False, "note": "fewer than 2 usable points"}
    ba, bc = tafel_slope_per_V(alpha, T), tafel_slope_per_V(alpha if alpha_c is None else alpha_c, T)
    slope = np.where(p["branch"] == "charge", ba, bc)
    L = p["log_i"].to_numpy() - slope * np.abs(p["eta_V"].to_numpy())
    L0 = float(np.mean(L))
    resid = L - L0
    i0 = 10 ** L0
    return {"method": "excel", "ok": True, "i0_A_cm2": i0, "log_i0": L0, "log_i0_se": float(np.std(resid, ddof=1) / np.sqrt(len(L))) if len(L) > 1 else np.nan,
            "alpha_a": alpha, "alpha_c": alpha if alpha_c is None else alpha_c,
            "Rct_ohm_cm2": _rct(i0, T), "rms_log": float(np.sqrt(np.mean(resid ** 2))), "n": len(p)}


def fit_butler_volmer(pts, T, start=None) -> dict:
    """i = i0 [exp(aa f eta) - exp(-ac f eta)], fitted in log|i| space to all usable points."""
    from scipy.optimize import least_squares
    p = _consistent(pts)
    if len(p) < 4:
        return {"method": "butler_volmer", "ok": False, "note": "fewer than 4 usable points"}
    f = FARADAY / (R_GAS * T)
    eta, logi = p["eta_V"].to_numpy(float), p["log_i"].to_numpy(float)

    def model_log(x):
        L0, aa, ac = x
        i = 10 ** L0 * (np.exp(aa * f * eta) - np.exp(-ac * f * eta))
        return np.log10(np.abs(i) + 1e-300)

    x0 = [start if start is not None and np.isfinite(start) else float(np.median(logi)) - 1, 0.5, 0.5]
    best = None
    for L0 in (x0[0], x0[0] - 1, x0[0] + 1):
        for a in (0.3, 0.5, 0.8):
            try:
                r = least_squares(lambda x: model_log(x) - logi, [L0, a, a],
                                  bounds=([-20, 0.01, 0.01], [5, 3.0, 3.0]))
            except Exception:  # noqa: BLE001
                continue
            if best is None or r.cost < best.cost:
                best = r
    if best is None:
        return {"method": "butler_volmer", "ok": False, "note": "fit did not converge"}
    resid = best.fun
    dof = max(len(logi) - 3, 1)
    try:
        cov = np.linalg.inv(best.jac.T @ best.jac) * (resid @ resid) / dof
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        se = [np.nan] * 3
    L0, aa, ac = best.x
    i0 = 10 ** L0
    at_bound = any(abs(v - b) < 1e-3 for v, b in ((aa, 0.01), (aa, 3.0), (ac, 0.01), (ac, 3.0)))
    return {"method": "butler_volmer", "ok": True, "i0_A_cm2": i0, "log_i0": L0, "log_i0_se": se[0],
            "alpha_a": aa, "alpha_a_se": se[1], "alpha_c": ac, "alpha_c_se": se[2],
            "Rct_ohm_cm2": _rct(i0, T), "rms_log": float(np.sqrt(np.mean(resid ** 2))), "n": len(p),
            "note": "alpha at its limit — data do not constrain it" if at_bound else ""}


def fit_tafel_region(pts, T, eta_min=0.05) -> dict:
    """Linear log|i| vs eta per branch, only |eta| >= eta_min."""
    p = _consistent(pts)
    res = {"method": "tafel_region", "ok": False, "n": 0}
    logs, alphas = [], {}
    for branch, key in (("charge", "alpha_a"), ("discharge", "alpha_c")):
        b = p[(p["branch"] == branch) & (p["eta_V"].abs() >= eta_min)]
        res[f"n_{branch}"] = len(b)
        if len(b) < 3:
            continue
        slope, icpt = np.polyfit(b["eta_V"], b["log_i"], 1)
        alphas[key] = abs(slope) * LN10 * R_GAS * T / FARADAY
        logs.append(icpt)
        res[f"log_i0_{branch}"] = icpt
        res["n"] += len(b)
    if not logs:
        res["note"] = f"fewer than 3 points with |eta| >= {eta_min * 1000:.0f} mV on both branches"
        return res
    L0 = float(np.mean(logs))
    res.update(ok=True, i0_A_cm2=10 ** L0, log_i0=L0, Rct_ohm_cm2=_rct(10 ** L0, T), **alphas)
    return res


def fit_tafel_extrapolation(pts, T, eta_min=0.05) -> dict:
    """
    Tafel extrapolation to the OCV: two straight lines fitted to the points, one per branch,
    forced to meet at eta = 0 (the OCV):
        log|i| = log i0 + s_a * eta      (charge, eta > 0)
        log|i| = log i0 + s_c * |eta|    (discharge, eta < 0)
    One linear least-squares fit (3 parameters) — the slopes come from the data (not a fixed
    alpha), so the lines pass through the points, and the crossing point IS log i0.
    Uses |eta| >= eta_min (Tafel region); if a branch has fewer than 2 such points, all of
    that branch's usable points are used (noted).
    """
    p = _consistent(pts)
    note = []
    rows = []
    for branch in ("charge", "discharge"):
        b = p[p["branch"] == branch]
        far = b[b["eta_V"].abs() >= eta_min]
        if len(far) < 2:
            if len(b) >= 2:
                note.append(f"{branch}: fewer than 2 points with |eta| >= {eta_min * 1000:.0f} mV, all {len(b)} used")
            far = b
        rows.append(far)
    q = pd.concat(rows) if rows else p.iloc[:0]
    n_c, n_d = (q["branch"] == "charge").sum(), (q["branch"] == "discharge").sum()
    if n_c < 2 or n_d < 2:
        return {"method": "tafel", "ok": False, "note": "fewer than 2 usable points on a branch"}
    eta = q["eta_V"].to_numpy(float)
    y = q["log_i"].to_numpy(float)
    ch = (q["branch"] == "charge").to_numpy()
    X = np.column_stack([np.ones_like(eta), np.where(ch, eta, 0.0), np.where(ch, 0.0, np.abs(eta))])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(len(y) - 3, 1)
    try:
        se = np.sqrt(np.diag(np.linalg.inv(X.T @ X) * (resid @ resid) / dof))
    except np.linalg.LinAlgError:
        se = [np.nan] * 3
    L0, sa, sc = (float(v) for v in coef)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    k = LN10 * R_GAS * T / FARADAY
    i0 = 10 ** L0
    return {"method": "tafel", "ok": sa > 0 and sc > 0, "i0_A_cm2": i0, "log_i0": L0, "log_i0_se": float(se[0]),
            "slope_a_dec_per_V": sa, "slope_c_dec_per_V": sc,
            "tafel_slope_a_mV_dec": 1000 / sa if sa > 0 else np.nan,
            "tafel_slope_c_mV_dec": 1000 / sc if sc > 0 else np.nan,
            "alpha_a": sa * k, "alpha_c": sc * k,
            "Rct_ohm_cm2": _rct(i0, T), "r2": 1 - float(resid @ resid) / ss_tot if ss_tot > 0 else np.nan,
            "rms_log": float(np.sqrt(np.mean(resid ** 2))), "n": len(q), "note": "; ".join(note)}


def fit_linear(pts, T, eta_max=0.015) -> dict:
    """i = i0 F eta/(RT) near equilibrium: slope through the origin of i vs eta."""
    p = _consistent(pts)
    p = p[p["eta_V"].abs() <= eta_max]
    if len(p) < 2:
        return {"method": "linear", "ok": False, "note": f"fewer than 2 points with |eta| <= {eta_max * 1000:.0f} mV"}
    eta, i = p["eta_V"].to_numpy(float), p["i_A_cm2"].to_numpy(float)
    k = float((i * eta).sum() / (eta * eta).sum())          # A cm-2 V-1
    i0 = k * R_GAS * T / FARADAY
    return {"method": "linear", "ok": i0 > 0, "i0_A_cm2": i0, "log_i0": math.log10(i0) if i0 > 0 else np.nan,
            "Rct_ohm_cm2": 1 / k if k > 0 else np.nan, "n": len(p)}


# ──────────────────────────────────────────────────────────────────────
# One sample
# ──────────────────────────────────────────────────────────────────────
@dataclass
class TafelState:
    label: object
    file: str
    ocv_V: float
    points: pd.DataFrame
    fits: Dict[str, dict] = field(default_factory=dict)
    R_ir_ohm: float = np.nan


@dataclass
class TafelSample:
    sample: str
    area_cm2: float
    geometry: dict
    states: List[TafelState]
    settings: dict

    def results(self) -> pd.DataFrame:
        rows = []
        for s in self.states:
            for m, f in s.fits.items():
                rows.append({"sample": self.sample, "state": s.label, "file": Path(s.file).name, "OCV_V": s.ocv_V,
                             "R_ir_Mohm": s.R_ir_ohm / 1e6 if np.isfinite(s.R_ir_ohm) else np.nan,
                             "method": m, "ok": f.get("ok", False),
                             "i0_mA_cm2": f.get("i0_A_cm2", np.nan) * 1e3,
                             "Rct_ohm_cm2": f.get("Rct_ohm_cm2", np.nan),
                             "alpha_a": f.get("alpha_a", np.nan), "alpha_c": f.get("alpha_c", np.nan),
                             "tafel_slope_a_mV_dec": f.get("tafel_slope_a_mV_dec", np.nan),
                             "tafel_slope_c_mV_dec": f.get("tafel_slope_c_mV_dec", np.nan),
                             "r2": f.get("r2", np.nan), "log_i0_se": f.get("log_i0_se", np.nan), "rms_log": f.get("rms_log", np.nan),
                             "n_points": f.get("n", 0), "note": f.get("note", "")})
        return pd.DataFrame(rows)

    def points(self) -> pd.DataFrame:
        return pd.concat([s.points.assign(sample=self.sample, state=s.label, OCV_V=s.ocv_V) for s in self.states],
                         ignore_index=True) if self.states else pd.DataFrame()


def analyse_sample(sample: str, spec: dict, verbose: bool = True) -> TafelSample:
    """Every pulse file in the sample's folder -> states with points and the four fits."""
    T = float(spec.get("temperature_K", 298.15))
    geom = particle_geometry(spec)
    area = geom["area_cm2"]
    files = pulse_files(spec["folder"], spec.get("file_pattern", "*.txt"))
    states_raw = []
    for f in files:
        st = analyse_state(read_pulse_file(f), zero_current=spec.get("zero_current_A"),
                           pulse_max_s=float(spec.get("pulse_max_s", 30)),
                           ocv_rest_min_s=float(spec.get("ocv_rest_min_s", 300)),
                           tafel_pulse_s=spec.get("tafel_pulse_s", "auto"))
        if st.is_pulse_file:
            states_raw.append(st)
    only = spec.get("files")                      # optional explicit list of file names
    if only:
        states_raw = [s for s in states_raw if Path(s.source).name in set(only)]
    labels = state_labels(spec, len(states_raw))
    methods = spec.get("methods", METHODS)
    out = []
    for lab, st in zip(labels, states_raw):
        pts = tafel_points(st, area, spec.get("overpotential", "ocv"), spec.get("repeat", "last"),
                           spec.get("current_range_A"), spec.get("currents_A"), spec.get("ir_correction"))
        fits = {}
        if "tafel" in methods:
            fits["tafel"] = fit_tafel_extrapolation(pts, T, float(spec.get("tafel_min_eta_V", 0.05)))
        if "excel" in methods:
            fits["excel"] = fit_excel(pts, T, float(spec.get("fixed_alpha", 0.5)), spec.get("fixed_alpha_c"))
        if "butler_volmer" in methods:
            fits["butler_volmer"] = fit_butler_volmer(pts, T, start=fits.get("excel", {}).get("log_i0"))
        if "tafel_region" in methods:
            fits["tafel_region"] = fit_tafel_region(pts, T, float(spec.get("tafel_region_min_eta_V", 0.05)))
        if "linear" in methods:
            fits["linear"] = fit_linear(pts, T, float(spec.get("linear_max_eta_V", 0.015)))
        out.append(TafelState(label=lab, file=st.source, ocv_V=st.ocv_V, points=pts, fits=fits,
                              R_ir_ohm=float(pts["R_ir_ohm"].iloc[0]) if len(pts) else np.nan))
    if verbose:
        print(f"  {sample}: {len(out)} states from {len(files)} files "
              f"(area {area:.3e} cm², {geom['kind']})")
    return TafelSample(sample=sample, area_cm2=area, geometry=geom, states=out, settings=spec)


# ──────────────────────────────────────────────────────────────────────
# Checking against a hand-built workbook
# ──────────────────────────────────────────────────────────────────────
def read_reference_workbook(path) -> pd.DataFrame:
    """
    The professor's Tafel workbook: one 'DOD<n>' sheet per state with the OCV (B21),
    alpha (F19), and the hand-set result: i0 in mA/cm2 (H31) and Rct (I31).
    """
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    rows = []
    for name in wb.sheetnames:
        n = "".join(ch for ch in name if ch.isdigit())
        if not name.upper().startswith(("DOD", "SOC")) or not n:
            continue
        ws = wb[name]

        def v(cell):
            try:
                x = ws[cell].value
                return float(x) if x is not None else np.nan
            except (TypeError, ValueError):
                return np.nan
        rows.append({"state": int(n), "excel_OCV_V": v("B21"), "excel_alpha": v("F19"),
                     "excel_slope_per_V": v("F20"), "excel_i0_mA_cm2": v("H31"), "excel_Rct_ohm_cm2": v("I31")})
    wb.close()
    return pd.DataFrame(rows).sort_values("state").reset_index(drop=True)


def compare_with_reference(sample: TafelSample) -> pd.DataFrame:
    """Code (excel method) vs the hand-built workbook, state by state."""
    ref = sample.settings.get("reference_workbook")
    if not ref or not Path(ref).exists():
        return pd.DataFrame()
    wb = read_reference_workbook(ref)
    code = sample.results()
    code = code[code["method"] == "excel"][["state", "OCV_V", "i0_mA_cm2", "Rct_ohm_cm2"]]
    code = code.rename(columns={"OCV_V": "code_OCV_V", "i0_mA_cm2": "code_i0_mA_cm2", "Rct_ohm_cm2": "code_Rct_ohm_cm2"})
    m = wb.merge(code, on="state", how="outer")
    m.insert(0, "sample", sample.sample)
    m["i0_ratio_code/excel"] = m["code_i0_mA_cm2"] / m["excel_i0_mA_cm2"]
    return m
