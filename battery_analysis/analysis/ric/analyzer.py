"""
RIC degradation-mechanism analysis — ported from the original ric.py's
RICAnalyzer, reading points back from the database instead of an Excel
sheet, and producing the same 4-part read (SEI growth, disconnection,
overall RIC/CE, dominant mechanism + recommendation) as a narrative
string, matching the shape of gcd/xps/dqdv's analyzers.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import linregress


def analyze_sei_growth(cycle: np.ndarray, ric_sei: np.ndarray, trend_window: int = 30) -> dict:
    """
    High RIC_SEI -> continuous SEI formation (poor coating).
    Increasing RIC_SEI -> accelerating SEI growth (particle cracking
    exposes fresh surface). Stable low RIC_SEI -> good SEI stability.
    """
    avg_sei = float(np.mean(ric_sei)) if len(ric_sei) else 0.0

    cycles_for_trend = cycle[:-1][:trend_window]
    sei_for_trend = ric_sei[:trend_window] if len(ric_sei) >= trend_window else ric_sei

    if len(sei_for_trend) > 5:
        slope, _, r_value, _, _ = linregress(cycles_for_trend[:len(sei_for_trend)], sei_for_trend)
    else:
        slope, r_value = 0.0, 0.0

    if avg_sei > 0.03:
        severity, interpretation = "High", "Severe continuous SEI formation"
    elif avg_sei > 0.015:
        severity, interpretation = "Moderate", "Moderate SEI growth"
    else:
        severity, interpretation = "Low", "Well-controlled SEI"

    if slope > 0.0005:
        trend, mechanism = "Accelerating", "Likely particle cracking exposing fresh surfaces"
    elif slope > 0:
        trend, mechanism = "Increasing", "Gradual SEI thickening"
    else:
        trend, mechanism = "Stable", "SEI passivation achieved"

    return {
        "avg_sei": avg_sei, "severity": severity, "interpretation": interpretation,
        "slope": float(slope), "trend": trend, "mechanism": mechanism,
        "r_squared": float(r_value ** 2),
    }


def analyze_disconnection(cycle: np.ndarray, ric_disconn: np.ndarray, trend_window: int = 30) -> dict:
    """
    High RIC_DISCONN -> severe particle pulverization/binder failure.
    Positive values -> loss of electrical contact. Negative values ->
    some capacity recovery (relaxation, SEI stabilization).
    """
    if len(ric_disconn) == 0:
        return {"avg_disconn": 0.0, "severity": "Low", "interpretation": "Not enough cycles",
                "pct_positive": 0.0, "slope": 0.0}

    avg_disconn = float(np.mean(ric_disconn))
    positive = ric_disconn[ric_disconn > 0]
    pct_positive = 100 * len(positive) / len(ric_disconn)

    cycles_for_trend = cycle[:-1][:trend_window]
    disconn_for_trend = ric_disconn[:trend_window] if len(ric_disconn) >= trend_window else ric_disconn
    if len(disconn_for_trend) > 5:
        slope, _, _, _, _ = linregress(cycles_for_trend[:len(disconn_for_trend)], disconn_for_trend)
    else:
        slope = 0.0

    if avg_disconn > 0.02:
        severity, interpretation = "Critical", "Severe particle pulverization/contact loss"
    elif avg_disconn > 0.01:
        severity, interpretation = "High", "Significant active material disconnection"
    elif avg_disconn > 0:
        severity, interpretation = "Moderate", "Some contact loss occurring"
    else:
        severity, interpretation = "Low", "Good mechanical stability"

    return {
        "avg_disconn": avg_disconn, "severity": severity, "interpretation": interpretation,
        "pct_positive": float(pct_positive), "slope": float(slope),
    }


def analyze_overall_ric(cycle: np.ndarray, ric: np.ndarray) -> dict:
    """High RIC -> poor coulombic efficiency. Increasing RIC -> accelerating degradation."""
    avg_ric = float(np.mean(ric)) if len(ric) else 0.0
    if len(ric) > 5:
        n = min(30, len(ric))
        slope, _, _, _, _ = linregress(cycle[:n], ric[:n])
    else:
        slope = 0.0
    ce = 100 / (1 + ric)
    avg_ce = float(np.mean(ce)) if len(ce) else 0.0
    return {
        "avg_ric": avg_ric, "avg_ce": avg_ce, "slope": float(slope),
        "trend": "Worsening" if slope > 0 else "Improving",
    }


def dominant_degradation_mode(sei: dict, disconn: dict) -> dict:
    sei_abs = abs(sei["avg_sei"])
    disconn_abs = abs(disconn["avg_disconn"])

    if sei_abs > 2 * disconn_abs:
        dominant = "SEI Growth"
        recommendation = "Focus on electrolyte optimization, use SEI-forming additives (FEC, VC)"
    elif disconn_abs > 2 * sei_abs:
        dominant = "Particle Disconnection"
        recommendation = "Improve binder (use CMC, PAA), reduce particle size, optimize electrode compression"
    else:
        dominant = "Mixed (SEI + Disconnection)"
        recommendation = "Address both: better binder + electrolyte additives"

    return {
        "dominant": dominant, "sei_contribution": sei_abs,
        "disconnection_contribution": disconn_abs, "recommendation": recommendation,
    }


def analyze_sample(cycle, cc, cd, ric, ric_disconn, ric_sei, sample_name: str, trend_window: int = 30) -> dict:
    overall = analyze_overall_ric(cycle, ric)
    sei = analyze_sei_growth(cycle, ric_sei, trend_window)
    disconn = analyze_disconnection(cycle, ric_disconn, trend_window)
    mode = dominant_degradation_mode(sei, disconn)
    return {
        "sample": sample_name, "overall_ric": overall, "sei_growth": sei,
        "disconnection": disconn, "degradation_mode": mode,
    }


def narrative(result: dict) -> str:
    """Render one sample's analyze_sample() result as Markdown, matching
    the other techniques' story()-ready narrative format."""
    sample = result["sample"]
    overall, sei, disconn, mode = (
        result["overall_ric"], result["sei_growth"], result["disconnection"], result["degradation_mode"]
    )
    lines = [f"**{sample}**"]
    lines.append(
        f"- Average RIC: {overall['avg_ric']:.4f} (mean CE {overall['avg_ce']:.2f}%, {overall['trend']})"
    )
    lines.append(
        f"- SEI growth: {sei['severity']} ({sei['avg_sei']:.4f}, {sei['trend']}) — {sei['interpretation']}; {sei['mechanism']}"
    )
    lines.append(
        f"- Particle disconnection: {disconn['severity']} ({disconn['avg_disconn']:.4f}, "
        f"{disconn['pct_positive']:.0f}% of cycles positive) — {disconn['interpretation']}"
    )
    lines.append(f"- Dominant degradation mode: {mode['dominant']} → {mode['recommendation']}")
    return "\n".join(lines)
