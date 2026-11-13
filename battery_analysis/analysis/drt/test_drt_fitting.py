"""
Ground-truth checks for the DRT solver.

A synthetic spectrum R_inf + L + two parallel RC elements has a known
answer: R_inf, L, the polarization resistance (R1 + R2 = area under γ),
and two peaks at τ1 = R1·C1 and τ2 = R2·C2. If the solver can't recover
those from a clean spectrum, it can't be trusted on real data.

Run:  python -m pytest battery_analysis/analysis/drt
"""
from __future__ import annotations

import numpy as np

from battery_analysis.analysis.drt.fitting import compute_drt, compute_drt_result

R_INF, R1, TAU1, R2, TAU2 = 5.0, 10.0, 1e-4, 20.0, 1e-1


def _spectrum(L=0.0, noise=0.0, seed=0):
    freq = np.logspace(5, -2, 71)
    w = 2 * np.pi * freq
    z = R_INF + 1j * w * L + R1 / (1 + 1j * w * TAU1) + R2 / (1 + 1j * w * TAU2)
    if noise:
        rng = np.random.default_rng(seed)
        z = z + noise * np.abs(z) * (rng.standard_normal(len(z)) + 1j * rng.standard_normal(len(z)))
    # Ivium/EIS-pipeline convention: report -Z'' (positive for capacitive arcs)
    return freq, z.real, -z.imag


def _polarization(tau, gamma):
    return float(np.trapezoid(gamma, np.log(tau)))


def test_recovers_r_inf_and_polarization():
    freq, zr, zi = _spectrum()
    tau, gamma, r_inf, _ = compute_drt(freq, zr, zi)
    assert abs(r_inf - R_INF) / R_INF < 0.05
    assert abs(_polarization(tau, gamma) - (R1 + R2)) / (R1 + R2) < 0.05


def test_gamma_is_non_negative():
    freq, zr, zi = _spectrum(noise=0.005)
    _, gamma, _, _ = compute_drt(freq, zr, zi)
    assert (gamma >= 0).all()


def test_finds_both_peaks_at_right_tau():
    freq, zr, zi = _spectrum()
    result = compute_drt_result(freq, zr, zi)
    taus = sorted(p.tau_peak for p in result.peaks)
    assert len(taus) == 2
    assert abs(np.log10(taus[0]) - np.log10(TAU1)) < 0.2
    assert abs(np.log10(taus[1]) - np.log10(TAU2)) < 0.2


def test_inductance_is_fitted_with_correct_sign():
    L = 1e-6
    freq, zr, zi = _spectrum(L=L)
    tau, gamma, r_inf, l_ind = compute_drt(freq, zr, zi, inductance=True)
    assert abs(l_ind - L) / L < 0.2
    assert abs(_polarization(tau, gamma) - (R1 + R2)) / (R1 + R2) < 0.05


def test_inductance_off_on_purely_capacitive_spectrum():
    freq, zr, zi = _spectrum()
    _, _, r_inf, l_ind = compute_drt(freq, zr, zi, inductance=True)
    # Allow a tiny leftover from the finite τ grid: its reactance at the
    # highest frequency must stay under 1 % of R_inf (negligible).
    assert 2 * np.pi * freq.max() * l_ind < 0.01 * r_inf


def test_peak_descriptions_do_not_assert_physics():
    freq, zr, zi = _spectrum()
    result = compute_drt_result(freq, zr, zi)
    for p in result.peaks:
        assert "SEI" not in p.description.split("(")[0]
