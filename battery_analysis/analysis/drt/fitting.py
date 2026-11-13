"""
DRT computation — RBF-Tikhonov methodology of DRTtools
(Wan et al. 2015, Electrochimica Acta 184, 483-499).

The impedance is modelled as

    Z(ω) = R_inf + jωL + ∫ γ(ln τ) / (1 + jωτ) d ln τ,
    γ(ln τ) = Σ_k x_k φ_k(ln τ)          (φ_k = RBF centred at ln τ_k)

and the weights x_k, R_inf and L are found together by non-negative,
Tikhonov-regularised least squares. Three things matter for correctness
and are checked by test_drt_fitting.py against spectra with a known answer:

  - γ(τ) is RECONSTRUCTED from the RBF weights (Φ·x). The weights
    themselves are not γ — returning them under-reports the polarization
    resistance (area under γ) and produces spurious peaks.
  - γ ≥ 0 is a CONSTRAINT of the solve (NNLS), not a clip afterwards.
    Clipping a solution that went negative distorts peak heights/areas.
  - R_inf (and L, when inductance=True) are FITTED, not read off the
    first data point, so a spectrum that doesn't reach the real axis at
    high frequency doesn't shift the whole DRT.

Peaks are labelled P1, P2, ... from high to low frequency. Which physical
process each one is (SEI, charge transfer, diffusion, contact...) is NOT
decided by the order — that needs evidence such as temperature, SOC or
cycling dependence, or symmetric-cell measurements.
"""
from __future__ import annotations

from typing import List

import numpy as np
from scipy.optimize import nnls
from scipy.signal import find_peaks

from .models import DRTPeak, DRTResult


# =========================================================
# RBF KERNELS
# =========================================================

def _rbf_gaussian(x, mu, epsilon):
    """Gaussian RBF:  exp(-(ε(x-μ))²)"""
    return np.exp(-(epsilon * (x - mu)) ** 2)


def _rbf_c2_matern(x, mu, epsilon):
    """C2 Matérn RBF:  (1 + ε|x-μ|) exp(-ε|x-μ|)"""
    r = epsilon * np.abs(x - mu)
    return (1.0 + r) * np.exp(-r)


def _rbf_c4_matern(x, mu, epsilon):
    """C4 Matérn RBF:  (1 + ε|x-μ| + ε²|x-μ|²/3) exp(-ε|x-μ|)"""
    r = epsilon * np.abs(x - mu)
    return (1.0 + r + r ** 2 / 3.0) * np.exp(-r)


def _rbf_c6_matern(x, mu, epsilon):
    """C6 Matérn RBF:  (1 + ε|x-μ| + 2ε²|x-μ|²/5 + ε³|x-μ|³/15) exp(-ε|x-μ|)"""
    r = epsilon * np.abs(x - mu)
    return (1.0 + r + 2.0 * r ** 2 / 5.0 + r ** 3 / 15.0) * np.exp(-r)


def _rbf_inverse_quadratic(x, mu, epsilon):
    """Inverse Quadratic RBF:  1 / (1 + (ε(x-μ))²)"""
    return 1.0 / (1.0 + (epsilon * (x - mu)) ** 2)


def _rbf_inverse_multiquadric(x, mu, epsilon):
    """Inverse Multiquadric RBF:  1 / sqrt(1 + (ε(x-μ))²)"""
    return 1.0 / np.sqrt(1.0 + (epsilon * (x - mu)) ** 2)


def _rbf_cauchy(x, mu, epsilon):
    """Cauchy RBF:  1 / (1 + ε²(x-μ)²)  (same as inverse quadratic with ε²)"""
    return 1.0 / (1.0 + (epsilon * (x - mu)) ** 2)


RBF_FUNCTIONS = {
    "gaussian": _rbf_gaussian,
    "c2_matern": _rbf_c2_matern,
    "c4_matern": _rbf_c4_matern,
    "c6_matern": _rbf_c6_matern,
    "inverse_quadratic": _rbf_inverse_quadratic,
    "inverse_multiquadric": _rbf_inverse_multiquadric,
    "cauchy": _rbf_cauchy,
}


def _rbf_basis(ln_tau, rbf_fn, epsilon):
    """Φ[j, k] = φ_k(ln τ_j): value of the k-th RBF at the j-th grid point."""
    return rbf_fn(ln_tau[:, None], ln_tau[None, :], epsilon)


def _build_rbf_matrices(omega, ln_tau, phi):
    """
    Re and Im kernel matrices mapping RBF weights x to impedance
    (Wan et al. 2015 Eqs. 14-15), integrated on the ln τ grid:

        Z'(ω_i)   - R_inf = Σ_j  1/(1+(ω_i τ_j)²)  · γ(τ_j) Δln τ_j
        -Z''(ω_i)         = Σ_j  ω_i τ_j/(1+(ω_i τ_j)²) · γ(τ_j) Δln τ_j

    with γ(τ_j) = Σ_k Φ[j, k] x_k.
    """
    dlnt = np.gradient(ln_tau)
    wt = omega[:, None] * np.exp(ln_tau)[None, :]
    denom = 1.0 + wt ** 2
    K_re = dlnt / denom
    K_im = wt * dlnt / denom
    return K_re @ phi, K_im @ phi


def _auto_epsilon(ln_tau, fwhm_coeff=0.5):
    """FWHM-based automatic shape parameter (DRTtools default)."""
    mean_dlnt = np.mean(np.diff(ln_tau))
    return fwhm_coeff / mean_dlnt


def _reg_matrix(n_tau, order=2):
    """
    Finite-difference regularization matrix L, applied to γ on the grid.
    order=1 -> first derivative penalty (smoother, less sharp peaks)
    order=2 -> second derivative penalty (DRTtools default, preserves peaks)
    """
    if order == 1:
        L = np.zeros((n_tau - 1, n_tau))
        for i in range(n_tau - 1):
            L[i, i] = -1
            L[i, i + 1] = 1
    else:
        L = np.zeros((n_tau - 2, n_tau))
        for i in range(n_tau - 2):
            L[i, i] = 1
            L[i, i + 1] = -2
            L[i, i + 2] = 1
    return L


_PYDRT_RBF = {
    "gaussian": "Gaussian", "c0_matern": "C0 Matern", "c2_matern": "C2 Matern",
    "c4_matern": "C4 Matern", "c6_matern": "C6 Matern",
    "inverse_quadratic": "Inverse Quadratic", "inverse_multiquadric": "Inverse Quadric",
    "cauchy": "Cauchy",
}
_PYDRT_DATA = {"combined": "Combined Re-Im Data", "re": "Re Data", "im": "Im Data"}


def compute_drt_pydrttools(freq, Z_real, Z_imag, lambda_reg=1e-3, rbf_type="gaussian",
                           data_use="combined", reg_order=2, fwhm_coeff=0.5,
                           inductance=False, lambda_selection="fixed", **_ignored):
    """
    DRT with pyDRTtools (Ciucci lab, https://github.com/ciuccislab/pyDRTtools) --
    runs.simple_run, the same ridge-regression (Tikhonov) RBF solve the DRTtools GUI
    runs (Wan et al. 2015). Returns (tau, gamma, R_inf, L_ind) like compute_drt.
    """
    import contextlib, io
    import matplotlib
    _backend = matplotlib.get_backend()
    with contextlib.redirect_stdout(io.StringIO()):      # pyDRTtools prints on import/solve
        from pyDRTtools import runs
        from cvxopt import solvers
    # pyDRTtools/__init__ imports its GUI, which calls mpl.use("Qt5Agg") and hijacks the
    # backend: plt.show() then blocks in a Qt event loop (scripts) or pops windows
    # (notebooks). Put the caller's backend back.
    if matplotlib.get_backend() != _backend:
        import matplotlib.pyplot as plt
        plt.switch_backend(_backend)
    solvers.options["show_progress"] = False

    freq = np.asarray(freq, float); Z_real = np.asarray(Z_real, float); Z_imag = np.asarray(Z_imag, float)
    ok = np.isfinite(freq) & np.isfinite(Z_real) & np.isfinite(Z_imag) & (freq > 0)
    freq, Z_real, Z_imag = freq[ok], Z_real[ok], Z_imag[ok]
    if freq.size < 5:
        raise ValueError("fewer than 5 valid frequency points")
    order = np.argsort(freq)[::-1]                        # high -> low frequency, as DRTtools expects
    freq, Z_real, Z_imag = freq[order], Z_real[order], Z_imag[order]
    # pyDRTtools wants Z = Z' + jZ'' with Z'' < 0 for capacitive arcs. Stored data may be -Z''.
    if np.median(Z_imag) > 0:
        Z_imag = -Z_imag

    try:
        rbf = _PYDRT_RBF[rbf_type.lower()]
        data_used = _PYDRT_DATA[data_use.lower()]
    except KeyError as e:
        raise ValueError(f"pyDRTtools has no option {e}; rbf_type: {list(_PYDRT_RBF)}, data_use: {list(_PYDRT_DATA)}")
    cv_type = "custom" if lambda_selection.lower() == "fixed" else lambda_selection

    entry = runs.EIS_object(freq, Z_real, Z_imag)
    with contextlib.redirect_stdout(io.StringIO()):
        runs.simple_run(entry, rbf_type=rbf, data_used=data_used,
                        induct_used=1 if inductance else 0,
                        der_used="1st order" if int(reg_order) == 1 else "2nd order",
                        cv_type=cv_type, reg_param=float(lambda_reg),
                        shape_control="FWHM Coefficient", coeff=float(fwhm_coeff))
    tau = np.asarray(entry.out_tau_vec, float)
    gamma = np.asarray(entry.gamma, float)
    srt = np.argsort(tau)
    return tau[srt], gamma[srt], float(entry.R), float(entry.L)


def compute_drt(
    freq, Z_real, Z_imag,
    n_tau=100,
    lambda_reg=1e-3,
    rbf_type="gaussian",
    data_use="combined",
    reg_order=2,
    fwhm_coeff=0.5,
    inductance=False,
    engine="pydrttools",
    lambda_selection="fixed",
):
    """
    Compute DRT γ(τ). engine="pydrttools" (default) hands the spectrum to
    pyDRTtools (compute_drt_pydrttools); engine="own" runs this package's own
    RBF-Tikhonov solver below, kept for cross-checks.

    Parameters
    ----------
    freq, Z_real, Z_imag : raw spectrum arrays. Either sign convention for
        the imaginary part is accepted (Z'' or -Z''); it is detected from
        the data so that capacitive arcs are positive internally.
    n_tau      : number of ln-tau collocation points (default 100)
    lambda_reg : Tikhonov regularization parameter (1e-3 = DRTtools default)
    rbf_type   : RBF kernel, see RBF_FUNCTIONS
    data_use   : "combined" (Re+Im, default) | "re" | "im"
    reg_order  : regularization derivative order, 1 or 2 (default)
    fwhm_coeff : RBF shape control via FWHM (default 0.5)
    inductance : if True, fit a series inductance L ≥ 0 together with γ

    Returns
    -------
    tau, gamma, R_inf, L_ind
        gamma is γ(τ) in Ω (per unit ln τ); its integral over ln τ is the
        total polarization resistance.
    """
    if engine.lower() == "pydrttools":
        return compute_drt_pydrttools(freq, Z_real, Z_imag, lambda_reg=lambda_reg, rbf_type=rbf_type,
                                      data_use=data_use, reg_order=reg_order, fwhm_coeff=fwhm_coeff,
                                      inductance=inductance, lambda_selection=lambda_selection)
    if engine.lower() != "own":
        raise ValueError(f"DRT engine must be 'pydrttools' or 'own', not {engine!r}")
    freq = np.asarray(freq, float)
    Z_real = np.asarray(Z_real, float)
    Z_imag = np.asarray(Z_imag, float)

    mask = np.isfinite(freq) & np.isfinite(Z_real) & np.isfinite(Z_imag) & (freq > 0)
    freq, Z_real, Z_imag = freq[mask], Z_real[mask], Z_imag[mask]
    idx = np.argsort(freq)[::-1]
    freq, Z_real, Z_imag = freq[idx], Z_real[idx], Z_imag[idx]

    if len(freq) < 7:
        raise ValueError(f"Only {len(freq)} valid EIS points — need >= 7.")

    # Capacitive arcs positive: -Z'' convention internally.
    Z_imag_cap = -Z_imag if np.nanmean(Z_imag) < 0 else Z_imag

    omega = 2.0 * np.pi * freq

    ln_tau = np.linspace(
        np.log(0.1 / (2.0 * np.pi * freq.max())),
        np.log(1.0 / (2.0 * np.pi * freq.min())),
        n_tau,
    )
    tau = np.exp(ln_tau)

    rbf_fn = RBF_FUNCTIONS.get(rbf_type, _rbf_gaussian)
    epsilon = _auto_epsilon(ln_tau, fwhm_coeff)
    phi = _rbf_basis(ln_tau, rbf_fn, epsilon)
    A_re, A_im = _build_rbf_matrices(omega, ln_tau, phi)

    # Extra unknowns: R_inf (real part only) and, optionally, L.
    # In the -Z'' convention an inductor contributes -ωL.
    n_f = len(freq)
    fit_r_inf = data_use != "im"
    fit_l = bool(inductance) and data_use != "re"
    extra_re, extra_im = [], []
    if fit_r_inf:
        extra_re.append(np.ones(n_f))
        extra_im.append(np.zeros(n_f))
    if fit_l:
        extra_re.append(np.zeros(n_f))
        extra_im.append(-omega)
    if extra_re:
        A_re = np.column_stack([A_re] + extra_re)
        A_im = np.column_stack([A_im] + extra_im)

    if data_use == "re":
        A, b = A_re, Z_real
    elif data_use == "im":
        A, b = A_im, Z_imag_cap
    else:
        A = np.vstack([A_re, A_im])
        b = np.concatenate([Z_real, Z_imag_cap])

    # Regularise the shape of γ = Φ x (not R_inf / L).
    n_extra = A.shape[1] - n_tau
    R = _reg_matrix(n_tau, reg_order) @ phi
    R = np.hstack([R, np.zeros((R.shape[0], n_extra))])

    # NNLS on the stacked system  [A; sqrt(λ) R] x ≈ [b; 0],  x ≥ 0.
    # φ ≥ 0 for every kernel above, so x ≥ 0 guarantees γ ≥ 0, R_inf ≥ 0, L ≥ 0.
    A_aug = np.vstack([A, np.sqrt(lambda_reg) * R])
    b_aug = np.concatenate([b, np.zeros(R.shape[0])])
    x, _ = nnls(A_aug, b_aug, maxiter=50 * A_aug.shape[1])

    gamma = phi @ x[:n_tau]
    extras = list(x[n_tau:])
    R_inf = float(extras.pop(0)) if fit_r_inf else float(Z_real[0])
    L_ind = float(extras.pop(0)) if fit_l else 0.0

    return tau, gamma, R_inf, L_ind


def find_drt_peaks(tau, gamma, prominence_frac=0.08, min_distance=6) -> List[DRTPeak]:
    """
    Find peaks in a DRT spectrum, labelled P1, P2, ... from high to low
    frequency. The description gives the characteristic frequency only —
    assigning a physical process is left to the analyst (see module docstring).
    """
    if gamma.max() <= 0:
        return []
    # Threshold relative to the tallest INTERIOR peak, not gamma.max():
    # a low-frequency diffusion/blocking tail makes γ rise to the edge of
    # the τ window, and measuring against that edge value hides every real
    # arc. Edge rises are not peaks (find_peaks never returns end points).
    candidates, _ = find_peaks(gamma)
    if len(candidates) == 0:
        return []
    reference = gamma[candidates].max()
    idx, _ = find_peaks(gamma, prominence=prominence_frac * reference, distance=min_distance)
    if len(idx) == 0:
        return []

    idx = idx[np.argsort(tau[idx])]
    peaks = []
    for i, j in enumerate(idx):
        f_peak = float(1.0 / (2.0 * np.pi * tau[j]))
        peaks.append(DRTPeak(
            label=f"P{i + 1}",
            description=(
                f"Process {i + 1} at {f_peak:.3g} Hz "
                "(assign physically, e.g. SEI / charge transfer / diffusion, from T/SOC dependence)"
            ),
            tau_peak=float(tau[j]),
            gamma_peak=float(gamma[j]),
            log_tau=float(np.log10(tau[j])),
            frequency_hz=f_peak,
        ))
    return peaks


def compute_drt_result(freq, z_real, z_imag, **kwargs) -> DRTResult:
    """Convenience wrapper: compute_drt + find_drt_peaks in one call."""
    tau, gamma, r_inf, l_ind = compute_drt(freq, z_real, z_imag, **kwargs)
    peaks = find_drt_peaks(tau, gamma)
    return DRTResult(tau=tau, gamma=gamma, r_inf=r_inf, l_ind=l_ind, peaks=peaks)
