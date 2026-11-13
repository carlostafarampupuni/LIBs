"""
Electrode / particle geometry for current densities and diffusion lengths.

A sample in tafel.yaml / gitt.yaml describes its electrode in ONE of three ways:

    area_cm2: 1.13                                  # a flat electrode (coin cell, film)

    particle:                                       # a single particle (microelectrode)
      radius_um: 6.75

    particle:                                       # ... or from its measured capacity
      capacity_nAh: 3.28
      specific_capacity_mAh_g: 1500
      density_g_cm3: 1.7

A spherical particle gives area 4*pi*r^2, volume 4/3*pi*r^3 and
volume/area = r/3 (the diffusion length used by GITT).
"""
from __future__ import annotations

import math
import re
from typing import Dict, Optional

FARADAY = 96485.33212        # C/mol
R_GAS = 8.314462618          # J/(mol K)

ATOMIC_MASS = {"H": 1.008, "Li": 6.941, "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Na": 22.990,
               "Mg": 24.305, "Al": 26.982, "Si": 28.086, "P": 30.974, "S": 32.06, "Cl": 35.45, "K": 39.098,
               "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845, "Co": 58.933,
               "Ni": 58.693, "Cu": 63.546, "Zn": 65.38, "Nb": 92.906, "Mo": 95.95, "Sn": 118.71}


def particle_geometry(spec: dict) -> Dict[str, float]:
    """
    {area_cm2, volume_cm3, radius_cm, diffusion_length_cm, kind} from a sample spec
    (see module docstring). diffusion_length = volume/area (r/3 for a sphere);
    for a flat electrode give `thickness_um` to get one.
    """
    if spec.get("area_cm2") is not None:
        area = float(spec["area_cm2"])
        thick = spec.get("thickness_um")
        vol = area * float(thick) * 1e-4 if thick else math.nan
        return {"kind": "flat", "area_cm2": area, "volume_cm3": vol, "radius_cm": math.nan,
                "diffusion_length_cm": float(thick) * 1e-4 if thick else math.nan}
    p = spec.get("particle") or {}
    if p.get("radius_um") is not None:
        r = float(p["radius_um"]) * 1e-4
    elif p.get("capacity_nAh") is not None:
        vol_cap_mAh_cm3 = float(p["specific_capacity_mAh_g"]) * float(p["density_g_cm3"])
        vol = float(p["capacity_nAh"]) * 1e-6 / vol_cap_mAh_cm3          # nAh -> mAh
        r = (3 * vol / (4 * math.pi)) ** (1 / 3)
    else:
        raise ValueError("Give area_cm2, or particle: {radius_um} / {capacity_nAh, specific_capacity_mAh_g, density_g_cm3}")
    area = 4 * math.pi * r ** 2
    vol = 4 / 3 * math.pi * r ** 3
    return {"kind": "particle", "area_cm2": area, "volume_cm3": vol, "radius_cm": r,
            "diffusion_length_cm": r / 3}


def molar_mass(formula: Dict[str, float]) -> float:
    """{'Li': 0.2, 'C': 8, 'H': 3, 'N': 1, 'S': 2} -> g/mol."""
    total = 0.0
    for el, n in formula.items():
        el = re.sub(r"[^A-Za-z]", "", str(el))
        if el not in ATOMIC_MASS:
            raise KeyError(f"Unknown element {el!r} — add it to ATOMIC_MASS in pulse/geometry.py")
        total += ATOMIC_MASS[el] * float(n)
    return total


def thermal_voltage(T_K: float) -> float:
    """RT/F in volts."""
    return R_GAS * T_K / FARADAY


def tafel_slope_per_V(alpha: float, T_K: float) -> float:
    """alpha*F/(2.303*R*T): decades of current per volt."""
    return alpha / (math.log(10) * thermal_voltage(T_K))
