"""
Fallback chemical names for imported (already-fitted) peaks that don't match
any component window in xps.yaml — used by sources.py. Binding-energy
windows from the original battery_analysis/xps.py.
"""
from __future__ import annotations


def get_peak_assignment(orbital: str, be: float) -> str:
    if orbital == "F 1s":
        if 684.0 <= be <= 685.5: return "LiF (inorganic SEI)"
        if 685.5 < be <= 687.0: return "LixPOyFz"
        if 687.0 < be <= 689.5: return "organic fluorides"
        return "unknown"
    if orbital == "P 2p":
        if 132.5 <= be <= 134.0: return "phosphate / LixPOy"
        if 134.0 < be <= 136.0: return "LixPOyFz"
        if 136.0 < be <= 138.0: return "PF6 residuals"
        return "unknown"
    if orbital == "O 1s":
        if 528.5 <= be <= 530.5: return "Li2O / metal oxide"
        if 530.5 < be <= 531.8: return "carbonate"
        if 531.8 < be <= 533.0: return "C-O (organic SEI)"
        if 533.0 < be <= 534.5: return "LiNO3 / LiNO2 / LiNxOy"
        return "unknown"
    if orbital == "C 1s":
        if 284.4 <= be <= 285.0: return "C-C / C-H"
        if 285.0 < be <= 286.5: return "C-O"
        if 286.5 < be <= 288.0: return "C=O"
        if 288.0 < be <= 290.0: return "carbonate / CFx"
        return "unknown"
    if orbital == "N 1s":
        if 396.5 <= be <= 398.0: return "Li3N"
        if 398.0 < be <= 399.5: return "LiNxOy (reduced)"
        if 399.5 < be <= 401.0: return "LiNO2"
        if 401.0 < be <= 403.0: return "LiNO3"
        return "unknown"
    if orbital == "Li 1s":
        if 54.0 <= be <= 55.0: return "Li metal"
        if 55.0 < be <= 55.8: return "Li2O / Li3N"
        if 55.8 < be <= 56.8: return "LiF"
        if 56.8 < be <= 58.0: return "Li2CO3 / LiNOx"
        return "unknown"
    if orbital == "Ti 2p":
        if 453.5 <= be <= 454.5: return "Ti-C (MXene)"
        if 454.5 < be <= 455.8: return "Ti2+"
        if 455.8 < be <= 457.2: return "Ti3+"
        if 457.2 < be <= 459.0: return "Ti4+ (TiO2)"
        return "unknown"
    if orbital == "Si 2p":
        if 98.5 <= be <= 99.5: return "Si0"
        if 99.5 < be <= 100.8: return "LixSi"
        if 100.8 < be <= 102.0: return "SiOx"
        if 102.0 < be <= 104.0: return "SiO2"
        return "unknown"
    return "not recognized"
