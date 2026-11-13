"""
EIS fitting: equivalent-circuit model fits (impedance.py) or frequency
extraction, chosen per batch in eis.yaml under `fitting:`.

Frequency extraction (use_model: false) is unchanged from the original
battery_analysis/eis.py: it reads Z' at fixed frequencies (Rs at 10 kHz,
...). That is a quick proxy, NOT a fit — those numbers are cumulative real
impedance at one frequency, not element resistances.

Model fitting (use_model: true) is automatic by default:

  initial guess   read from each spectrum (_estimate): Rs = high-frequency
                  intercept, Rct = width of the arc up to its valley, CPE Q
                  from the arc's apex frequency, the low-frequency CPE from
                  the slope of the tail, L from inductive high-frequency points.
  inductance      added (L0 in series) only when the spectrum shows inductive
                  points (Im Z > 0 at high frequency) — `inductance: auto`.
  circuit         `circuit_key: auto` fits every circuit in `candidates`
                  (default: all of CIRCUITS) to every spectrum in the batch and
                  keeps the ONE circuit with the lowest total AICc, so every
                  sample in a batch is described by the same model and its
                  Rct/Rs stay comparable. AICc rewards a better fit but
                  charges for every extra parameter; candidates whose fit runs
                  into a bound on any spectrum are passed over.
  weighting       residuals are weighted by |Z| (weight_by_modulus), so the
                  small high-frequency arc counts as much as the big
                  low-frequency tail.

Everything automatic can be pinned in eis.yaml when you know better:
circuit_key (a key below, or any impedance.py circuit string), candidates,
inductance, f_min/f_max, initial_guess {R1: 20, ...}.

Stored per spectrum: the friendly names the rest of the pipeline reads (Rs,
Rsei, Rct, W — only those the circuit has), the raw impedance.py parameters
(R0, CPE1_0, ...) so the plot can redraw the model, and fit_err_pct
(median |Z_fit - Z| / |Z|, in %). The circuit string itself is saved as
circuit_key.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from scipy.interpolate import interp1d

from .models import EISMeasurement, EISFitResult

# "names" maps the pipeline's parameter names onto impedance.py's. Battery
# circuits keep their meaning (Rsei = SEI film arc); in the supercapacitor
# circuits the high-frequency arc of SC2 is reported as Rsei (interface /
# contact film) so the same degradation and plot code works for both.
CIRCUITS = {
    "CT": {"circuit": "R0-p(R1,CPE1)-Wo1",
           "names": {"Rs": "R0", "Rct": "R1", "W": "Wo1_0"}},
    "SEI_CT": {"circuit": "R0-p(R1,CPE1)-p(R2,CPE2)",
               "names": {"Rs": "R0", "Rsei": "R1", "Rct": "R2"}},
    "SEI_CT_W": {"circuit": "R0-p(R1,CPE1)-p(R2,CPE2)-Wo1",
                 "names": {"Rs": "R0", "Rsei": "R1", "Rct": "R2", "W": "Wo1_0"}},
    # supercapacitor / blocking electrode: the tail is capacitive (CPE), not a diffusion arc
    "SC": {"circuit": "R0-p(R1,CPE1)-CPE2",
           "names": {"Rs": "R0", "Rct": "R1"}},
    "SC_W": {"circuit": "R0-p(R1,CPE1)-W1-CPE2",
             "names": {"Rs": "R0", "Rct": "R1", "W": "W1"}},
    "SC2": {"circuit": "R0-p(R1,CPE1)-p(R2,CPE2)-CPE3",
            "names": {"Rs": "R0", "Rsei": "R1", "Rct": "R2"}},
}


# ===========================================================================
# Frequency extraction + helpers (unchanged)
# ===========================================================================

def smooth_nyquist_data(Z_exp, freqs, smoothing_factor=0.1):
    window_size = max(3, int(len(Z_exp) * smoothing_factor))
    if window_size % 2 == 0:
        window_size += 1
    from scipy.ndimage import uniform_filter1d
    return (
        uniform_filter1d(Z_exp.real, size=window_size, mode="nearest")
        + 1j * uniform_filter1d(Z_exp.imag, size=window_size, mode="nearest")
    )


def extract_impedance_at_freq(freqs, Z_exp, target_freq):
    sort_idx = np.argsort(freqs)
    interp_func = interp1d(
        freqs[sort_idx], Z_exp.real[sort_idx],
        kind="linear", fill_value="extrapolate",
    )
    return interp_func(target_freq)


def _fix_sign_convention(zimag):
    """Return zimag negated if mean is positive (ensure -Z'' convention)."""
    with np.errstate(invalid="ignore"):
        mean_im = np.nanmean(zimag)
        if not np.isnan(mean_im) and mean_im > 0:
            return zimag * -1
    return zimag


# ===========================================================================
# Reading starting values off the spectrum
# ===========================================================================

def _estimate(freqs, Z) -> dict:
    """Physical starting values from the data alone (high -> low frequency)."""
    order = np.argsort(freqs)[::-1]
    f, zr, mz = freqs[order], Z.real[order], -Z.imag[order]      # mz = -Z'' (positive = capacitive)
    w = 2 * np.pi * f

    inductive = mz < 0
    L = float(np.max(-mz[inductive] / w[inductive])) if inductive[:3].any() else 0.0
    cap = np.where(~inductive)[0]
    i0 = cap[0] if len(cap) else 0
    if i0 > 0:                                                  # Rs where -Z'' crosses zero
        a, b = i0 - 1, i0
        Rs = zr[a] + (zr[b] - zr[a]) * (0 - mz[a]) / (mz[b] - mz[a])
    else:
        Rs = zr[0]
    Rs = float(max(Rs, 1e-3))

    # arc: first local maximum of -Z'' after the intercept, then the valley after it
    seg = mz[i0:]
    apex = valley = None
    for k in range(1, len(seg) - 1):
        if apex is None and seg[k] >= seg[k - 1] and seg[k] > seg[k + 1]:
            apex = k
        elif apex is not None and seg[k] <= seg[k - 1] and seg[k] < seg[k + 1]:
            valley = k
            break
    span = float(max(zr[-1] - Rs, 1e-2))
    if apex is not None and valley is not None:
        Rct = float(max(zr[i0 + valley] - Rs, 1e-2))
        f_apex = float(f[i0 + apex])
    else:
        Rct, f_apex = span / 3, float(np.sqrt(f[0] * f[-1]))
    n_dl = 0.8
    Q_dl = 1.0 / (Rct * (2 * np.pi * f_apex) ** n_dl)

    # tail: slope of log(-Z'') vs log(omega) over the lowest-frequency points gives -n
    k = min(6, len(f) - 1)
    good = mz[-k:] > 0
    if good.sum() >= 3:
        n_lf = float(np.clip(-np.polyfit(np.log(w[-k:][good]), np.log(mz[-k:][good]), 1)[0], 0.4, 1.0))
    else:
        n_lf = 0.8
    Q_lf = 1.0 / (max(mz[-1], 1e-6) * w[-1] ** n_lf * np.sin(n_lf * np.pi / 2))
    tail_r = float(max(zr[-1] - Rs - Rct, 1e-2))
    return {"L": L, "Rs": Rs, "Rct": Rct, "Q_dl": Q_dl, "n_dl": n_dl, "Q_lf": Q_lf, "n_lf": n_lf,
            "W": tail_r * np.sqrt(w[-1]), "Wo_R": tail_r, "Wo_tau": 1.0 / w[-1], "zmax": float(np.max(np.abs(Z)))}


def _elements(circuit: str) -> List[str]:
    return re.findall(r"[A-Za-z]+\d+", circuit)


def _guess_and_bounds(circuit: str, est: dict):
    """One starting value + (lower, upper) per impedance.py parameter, element by element.
    The first R is Rs, the next ones share the arc; the last CPE of a circuit that ends
    in a CPE is the low-frequency tail, every other CPE belongs to an arc."""
    zmax = est["zmax"]
    els = _elements(circuit)
    n_r = sum(re.match(r"[A-Za-z]+", e).group() == "R" for e in els) - 1
    cpes = [e for e in els if e.startswith("CPE")]
    tail_cpe = cpes[-1] if cpes and circuit.split("-")[-1].startswith("CPE") else None
    r_seen = arc_cpe = 0
    guess, lo, hi = [], [], []
    for e in els:
        kind = re.match(r"[A-Za-z]+", e).group()
        if kind == "L":
            guess += [max(est["L"], 1e-9)]; lo += [0]; hi += [1e-3]
        elif kind == "R":
            if r_seen == 0:
                g = est["Rs"]
            else:                                     # split the arc between the arcs present
                g = est["Rct"] * ((0.3 if r_seen == 1 else 0.7) if n_r > 1 else 1.0)
            r_seen += 1
            guess += [g]; lo += [0]; hi += [10 * zmax]
        elif kind == "CPE":
            if e == tail_cpe:
                guess += [est["Q_lf"], est["n_lf"]]
            else:
                guess += [est["Q_dl"] * 10 ** arc_cpe, est["n_dl"]]
                arc_cpe += 1
            lo += [1e-10, 0.3]; hi += [100.0, 1.0]
        elif kind in ("Wo", "Ws"):
            guess += [est["Wo_R"], est["Wo_tau"]]; lo += [0, 1e-6]; hi += [10 * zmax, 1e5]
        elif kind == "W":
            guess += [est["W"]]; lo += [0]; hi += [1e4 * max(zmax, 1)]
        elif kind == "C":
            guess += [1e-3]; lo += [1e-12]; hi += [100.0]
        else:
            raise ValueError(f"no automatic guess for element {e!r} in {circuit!r} — set initial_guess in eis.yaml")
    guess = [float(np.clip(g, l + 1e-9 * (h - l), h - 1e-9 * (h - l))) for g, l, h in zip(guess, lo, hi)]
    return guess, (lo, hi)


# ===========================================================================
# One fit
# ===========================================================================

@dataclass
class _Fit:
    circuit: str
    names: List[str]
    values: np.ndarray
    rel_err: float          # median |dZ|/|Z|
    aicc: float
    at_bound: List[str]


def _fit_one(circuit: str, freqs, Z, overrides: Optional[Dict[str, float]] = None) -> Optional[_Fit]:
    from impedance.models.circuits import CustomCircuit   # lazy: only needed when use_model is on

    est = _estimate(freqs, Z)
    guess, (lo, hi) = _guess_and_bounds(circuit, est)
    names = _param_names(circuit)
    for k, v in (overrides or {}).items():
        if k in names:
            i = names.index(k)
            guess[i] = float(np.clip(v, lo[i], hi[i]))
    c = CustomCircuit(circuit, initial_guess=guess)
    try:
        c.fit(freqs, Z, bounds=(lo, hi), weight_by_modulus=True, maxfev=20000)
    except Exception:
        return None
    p = np.asarray(c.parameters_, float)
    rel = np.abs(c.predict(freqs) - Z) / np.abs(Z)
    n, k = 2 * len(freqs), len(p)
    ss = float(np.sum(rel ** 2))
    aicc = n * np.log(ss / n) + 2 * k + (2 * k * (k + 1) / (n - k - 1) if n - k - 1 > 0 else np.inf)
    # "at a bound" relative to each parameter's own scale (its starting value),
    # not the bound range: a CPE Q of 2e-5 is nowhere near its 1e-10 floor.
    def _stuck(v, l, h, g):
        scale = max(abs(g), abs(v), 1e-15)
        return v - l <= 1e-4 * scale or h - v <= 1e-4 * scale
    # Physical limits, not failures: CPE n = 1 (ideal capacitor), L = 0 (no inductance),
    # W = 0 (no diffusion: the circuit simply reduces to the one without W).
    physical = lambda nm: nm.endswith("_1") or nm.startswith("L") or re.fullmatch(r"W\d+", nm)
    at_bound = [nm for nm, v, l, h, g in zip(names, p, lo, hi, guess) if _stuck(v, l, h, g) and not physical(nm)]
    return _Fit(circuit, names, p, float(np.median(rel)), float(aicc), at_bound)


def _with_inductance(circuit: str, freqs, Z, mode) -> str:
    if circuit.startswith("L") or mode is False:
        return circuit
    if mode is True or _estimate(freqs, Z)["L"] > 0:
        return "L0-" + circuit
    return circuit


def _params_from(fit: _Fit, key: Optional[str]) -> Dict[str, float]:
    raw = dict(zip(fit.names, fit.values))
    out = {}
    for friendly, raw_name in (CIRCUITS.get(key, {}).get("names") or {"Rs": "R0"}).items():
        if raw_name in raw:
            out[friendly] = float(raw[raw_name])
    out.update({k: float(v) for k, v in raw.items()})
    out["fit_err_pct"] = 100 * fit.rel_err
    return out


def _window(freqs, Z, f_min, f_max):
    m = np.ones(len(freqs), bool)
    if f_min is not None:
        m &= freqs >= f_min
    if f_max is not None:
        m &= freqs <= f_max
    return freqs[m], Z[m]


def _param_names(circuit: str) -> List[str]:
    from impedance.models.circuits import CustomCircuit
    from impedance.models.circuits.fitting import calculateCircuitLength
    return CustomCircuit(circuit, initial_guess=[1.0] * calculateCircuitLength(circuit)).get_param_names()[0]


def model_curve(circuit: str, params: Dict[str, float], freqs: np.ndarray) -> Optional[np.ndarray]:
    """Redraw a stored fit: Z(f) of `circuit` with its stored raw parameters, or None."""
    from impedance.models.circuits import CustomCircuit
    try:
        values = [params[n] for n in _param_names(circuit)]
        with warnings.catch_warnings():        # "simulating from initial parameters" is exactly the point here
            warnings.simplefilter("ignore")
            return CustomCircuit(circuit, initial_guess=values).predict(freqs, use_initial=True)
    except Exception:
        return None


# ===========================================================================
# Public entry points
# ===========================================================================

def _prepare(measurement: EISMeasurement):
    zreal = measurement.z_real
    zimag = _fix_sign_convention(measurement.z_imag)       # signed Im(Z), negative = capacitive
    mask = np.isfinite(zreal) & np.isfinite(zimag) & np.isfinite(measurement.freq)
    if not mask.any():
        return None, None
    return measurement.freq[mask], zreal[mask] + 1j * zimag[mask]


def fit_measurement(
    measurement: EISMeasurement,
    *,
    use_model: bool = False,
    circuit_key: str = "SEI_CT_W",
    extract_at_freq: bool = True,
    freq_extraction: Optional[Dict[str, float]] = None,
    inductance="auto",
    f_min: Optional[float] = None,
    f_max: Optional[float] = None,
    initial_guess: Optional[Dict[str, float]] = None,
) -> Optional[EISFitResult]:
    """Fit (or frequency-extract) one spectrum with one named circuit (no 'auto' here — see run_fit)."""
    freqs, Z = _prepare(measurement)
    if freqs is None:
        return None

    if not use_model and extract_at_freq:
        params: Dict[str, float] = {}
        for p_name, t_freq in (freq_extraction or {}).items():
            try:
                params[p_name] = float(extract_impedance_at_freq(freqs, Z, t_freq))
            except Exception:
                params[p_name] = float("nan")
        return EISFitResult(measurement, circuit_key, params, method="freq_extraction")

    if use_model:
        freqs, Z = _window(freqs, Z, f_min, f_max)
        base = CIRCUITS[circuit_key]["circuit"] if circuit_key in CIRCUITS else circuit_key
        circuit = _with_inductance(base, freqs, Z, inductance)
        fit = _fit_one(circuit, freqs, Z, initial_guess)
        if fit is None:
            print(f"⚠️  Fit failed for measurement {measurement.sheet_name} ({circuit})")
            return None
        return EISFitResult(measurement, circuit, _params_from(fit, circuit_key if circuit_key in CIRCUITS else None),
                            method="model_fit")
    return None


def _measurements(repo, project_id, batch_name, all_replicates=False):
    df = repo.list_eis_measurements(project_id, batch_name=batch_name)
    out = []
    for _, row in df.iterrows():
        if not all_replicates and not row["is_representative"]:
            continue
        spectrum = repo.load_eis_spectrum(row["measurement_id"])
        if spectrum.empty:
            continue
        out.append((row, EISMeasurement(
            cycle_number=row["cycle_number"], sheet_name=row["sheet_name"],
            freq=spectrum["freq"].to_numpy(), z_real=spectrum["z_real"].to_numpy(),
            z_imag=spectrum["z_imag"].to_numpy(), source_file=None)))
    return out


def _choose_circuit(items, fit_cfg) -> Optional[str]:
    """Fit every candidate to every spectrum; keep the circuit with the lowest summed AICc
    among those that fit every spectrum without running into a bound."""
    candidates = fit_cfg.candidates or list(CIRCUITS)
    table = []
    for key in candidates:
        fits = []
        for _, m in items:
            freqs, Z = _prepare(m)
            freqs, Z = _window(freqs, Z, fit_cfg.f_min, fit_cfg.f_max)
            base = CIRCUITS[key]["circuit"] if key in CIRCUITS else key
            fits.append(_fit_one(_with_inductance(base, freqs, Z, fit_cfg.inductance), freqs, Z, fit_cfg.initial_guess))
        ok = all(f is not None for f in fits)
        bounded = sorted({b for f in fits if f is not None for b in f.at_bound})
        table.append((key, ok and not bounded, sum(f.aicc for f in fits) if ok else np.inf,
                      np.median([f.rel_err for f in fits]) * 100 if ok else np.nan, bounded))
    print("   circuit     total AICc   median error   note")
    for key, valid, aicc, err, bounded in sorted(table, key=lambda t: t[2]):
        note = "" if valid else ("fit failed" if not np.isfinite(aicc) else f"hits a bound: {', '.join(bounded)}")
        print(f"   {key:<10} {aicc:>11.1f}   {err:>9.1f} %    {note}")
    valid = [t for t in table if t[1]]
    if not valid:
        best = min(table, key=lambda t: t[2])
        if not np.isfinite(best[2]):
            return None
        print(f"⚠️  every candidate hits a bound somewhere — using the best one anyway ({best[0]})")
        return best[0]
    return min(valid, key=lambda t: t[2])[0]


def run_fit(config, eis_cfg, repo, project_id: int, batch_name: str | None = None) -> int:
    """
    Fit/extract every representative spectrum of this batch and store the
    results (repository.save_eis_fit_result). With circuit_key: auto the
    circuit is chosen once for the whole batch (see _choose_circuit).
    Returns the number of spectra fitted.
    """
    fit_cfg = eis_cfg.fitting
    choose = getattr(eis_cfg, "representative", "first")
    if choose not in ("first", "best_fit", "lowest_Rct"):
        raise ValueError(f"representative: {choose!r} — use first, best_fit or lowest_Rct")
    items = _measurements(repo, project_id, batch_name, all_replicates=choose != "first")

    key = fit_cfg.circuit_key
    if fit_cfg.use_model and key == "auto" and items:
        print(f"🔎  Choosing a circuit for {len(items)} spectra (lowest total AICc wins):")
        key = _choose_circuit(items, fit_cfg)
        if key is None:
            print("⚠️  No candidate circuit could be fitted — nothing stored.")
            return 0
        print(f"✅  Circuit for this batch: {key} = {CIRCUITS.get(key, {}).get('circuit', key)}")

    n, scores = 0, []
    for row, measurement in items:
        result = fit_measurement(
            measurement, use_model=fit_cfg.use_model, circuit_key=key,
            extract_at_freq=fit_cfg.extract_at_freq, freq_extraction=fit_cfg.freq_extraction,
            inductance=fit_cfg.inductance, f_min=fit_cfg.f_min, f_max=fit_cfg.f_max,
            initial_guess=fit_cfg.initial_guess,
        )
        if result is not None:
            repo.save_eis_fit_result(row["measurement_id"], result.circuit_key, result.params, result.method)
            scores.append((row["sample"], int(row["experiment_id"]), row["source_folder"], result.params))
            if result.method == "model_fit":
                p = result.params
                shown = ", ".join(f"{k}={p[k]:.2f} Ω" for k in ("Rs", "Rsei", "Rct") if k in p)
                print(f"   {row['sample']} ({row['source_folder']}): {shown}  error {p['fit_err_pct']:.1f} %")
            n += 1

    if choose != "first":
        _pick_representatives(repo, eis_cfg, scores, choose)
    print(f"✅  Fit/extracted parameters for {n} EIS measurement(s).")
    return n


def _pick_representatives(repo, eis_cfg, scores, choose: str) -> None:
    """Per sample, keep the trial (cell) with the lowest fit_err_pct (best_fit) or Rct
    (lowest_Rct) as the plotted/analysed one. A sample_overrides plot: entry wins."""
    key = "fit_err_pct" if choose == "best_fit" else "Rct"
    by_sample: Dict[str, Dict[int, list]] = {}
    folder_of = {}
    for sample, exp_id, folder, params in scores:
        by_sample.setdefault(sample, {}).setdefault(exp_id, []).append(params.get(key, np.nan))
        folder_of[exp_id] = folder
    print(f"🏅  Plotted trial per sample ({'lowest fit error' if key == 'fit_err_pct' else 'lowest Rct'}):")
    for sample, trials in sorted(by_sample.items()):
        score = {e: float(np.nanmean(v)) if np.isfinite(v).any() else np.inf for e, v in trials.items()}
        override = eis_cfg.sample_override(sample)
        forced = [e for e in trials if override and override.plot and folder_of[e] in override.plot]
        best = forced[0] if forced else min(score, key=score.get)
        repo.set_representative(best, [e for e in trials if e != best])
        unit = "%" if key == "fit_err_pct" else "Ω"
        listing = ", ".join(f"{folder_of[e]} {score[e]:.1f} {unit}" for e in sorted(trials, key=lambda e: folder_of[e]))
        print(f"   {sample}: {listing}  ->  {folder_of[best]}" + ("  (sample_overrides)" if forced else ""))
