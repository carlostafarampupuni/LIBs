"""
Electrode disc calculations — loading, coating density, areal capacity,
and the current (mA) for every C-rate you test at.

Ported from the battery_lab_toolkit's electrode_lab/electrode.py, folded
directly into battery_analysis (flat module, no new folder) instead of
kept as a separate sibling project. Nothing about the math changed — see
test_lab_calc.py for the same numeric checks the toolkit shipped with.

This is a direct port of the "LFP Electrode" table pattern from a Numbers
spreadsheet (LFP Low Temperature / LFP Cathode sheets), generalized to
any active material, any punch size, any C-rate list.

Units, fixed throughout this module (say so once, never guess again):
    mass        grams (g)
    thickness   millimeters (mm)
    diameter    millimeters (mm)
    area        cm^2
    capacity    mAh/g (specific), mAh (absolute), mAh/cm^2 (areal)
    current     mA
    density     g/cc
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

DEFAULT_C_RATES = (0.05, 0.1, 0.5, 1, 3, 5, 7, 10, 15, 20)


@dataclass
class ElectrodeMeasurement:
    """
    One coated, punched electrode disc, as you'd measure it on the bench.

    Give EITHER diameter_mm OR area_cm2 — not both. Everything else is
    required; there's no silent default for a number that changes your
    answer (that's exactly the kind of thing a spreadsheet lets you
    forget to fill in and never tells you).
    """
    name: str                          # e.g. "Bare", "MX", "Cell-014"
    foil_weight_g: float                # tare weight of the bare (uncoated) punched disc
    foil_thickness_mm: float            # bare foil thickness
    disc_weight_g: float                # coated + dried + punched disc, total weight
    disc_thickness_mm: float            # coated disc, total thickness (after calendaring)
    am_fraction: float                  # active material weight fraction in the DRY coating, e.g. 0.8
    theoretical_capacity_mAhg: float    # specific capacity of the active material, e.g. 170 for LFP
    diameter_mm: Optional[float] = None
    area_cm2: Optional[float] = None
    c_rates: Sequence[float] = field(default_factory=lambda: DEFAULT_C_RATES)

    def __post_init__(self):
        if (self.diameter_mm is None) == (self.area_cm2 is None):
            raise ValueError(
                f"{self.name}: give exactly one of diameter_mm or area_cm2 (got "
                f"diameter_mm={self.diameter_mm!r}, area_cm2={self.area_cm2!r})."
            )
        if self.disc_weight_g <= self.foil_weight_g:
            raise ValueError(
                f"{self.name}: disc_weight_g ({self.disc_weight_g}) must be greater than "
                f"foil_weight_g ({self.foil_weight_g}) — the coated disc has to weigh more "
                f"than the bare foil it's punched from. Check for a units mismatch or a "
                f"swapped value."
            )
        if self.disc_thickness_mm <= self.foil_thickness_mm:
            raise ValueError(
                f"{self.name}: disc_thickness_mm ({self.disc_thickness_mm}) must be greater "
                f"than foil_thickness_mm ({self.foil_thickness_mm})."
            )
        if not (0 < self.am_fraction <= 1):
            raise ValueError(f"{self.name}: am_fraction must be in (0, 1], got {self.am_fraction}.")
        for label, val in [("foil_weight_g", self.foil_weight_g), ("disc_weight_g", self.disc_weight_g),
                            ("theoretical_capacity_mAhg", self.theoretical_capacity_mAhg)]:
            if val <= 0:
                raise ValueError(f"{self.name}: {label} must be > 0, got {val}.")

    # ---------------------------------------------------------------
    # computed properties — each one is one line, on purpose, so you
    # can see exactly which raw inputs feed which answer.
    # ---------------------------------------------------------------

    @property
    def area_cm2_resolved(self) -> float:
        if self.area_cm2 is not None:
            return self.area_cm2
        radius_cm = (self.diameter_mm / 2) / 10   # mm -> cm
        return math.pi * radius_cm ** 2

    @property
    def coating_mass_g(self) -> float:
        """Total dried coating mass (active material + binder + conductive additive), foil tare removed."""
        return self.disc_weight_g - self.foil_weight_g

    @property
    def am_mass_g(self) -> float:
        """Active-material-only mass on this disc."""
        return self.coating_mass_g * self.am_fraction

    @property
    def coating_thickness_mm(self) -> float:
        return self.disc_thickness_mm - self.foil_thickness_mm

    @property
    def coating_volume_cm3(self) -> float:
        """area(cm^2) x thickness(mm) / 10  ->  cm^3 (the /10 converts the mm thickness to cm)."""
        return self.area_cm2_resolved * self.coating_thickness_mm / 10

    @property
    def coating_density_gcc(self) -> float:
        return self.coating_mass_g / self.coating_volume_cm3

    @property
    def loading_mg_cm2(self) -> float:
        """Active-material areal loading — the number you quote in a paper/report."""
        return self.am_mass_g / self.area_cm2_resolved * 1000

    @property
    def areal_capacity_mAh_cm2(self) -> float:
        return self.loading_mg_cm2 / 1000 * self.theoretical_capacity_mAhg

    @property
    def capacity_1C_mA(self) -> float:
        """
        The '1C current' in mA — by definition, 1C is whatever current
        empties the full capacity in exactly 1 hour, so numerically this
        equals the disc's total capacity in mAh.
        """
        return self.am_mass_g * self.theoretical_capacity_mAhg

    def current_at_c_rate(self, c: float) -> float:
        """Current (mA) to run this disc at C-rate `c` (e.g. c=0.5 for C/2, c=20 for 20C)."""
        return self.capacity_1C_mA * c

    @property
    def currents_by_c_rate(self) -> dict:
        return {c: self.current_at_c_rate(c) for c in self.c_rates}

    def summary(self) -> dict:
        """Every computed value in one dict — what lab_batch.py / the notebook writes to a row."""
        d = {
            "name": self.name,
            "area_cm2": round(self.area_cm2_resolved, 6),
            "coating_mass_g": round(self.coating_mass_g, 6),
            "am_mass_g": round(self.am_mass_g, 6),
            "coating_thickness_mm": round(self.coating_thickness_mm, 6),
            "coating_volume_cm3": round(self.coating_volume_cm3, 8),
            "coating_density_gcc": round(self.coating_density_gcc, 6),
            "loading_mg_cm2": round(self.loading_mg_cm2, 6),
            "areal_capacity_mAh_cm2": round(self.areal_capacity_mAh_cm2, 6),
            "capacity_1C_mA": round(self.capacity_1C_mA, 6),
        }
        for c, mA in self.currents_by_c_rate.items():
            label = f"{c:g}C_mA"
            d[label] = round(mA, 6)
        return d

    def report(self) -> str:
        lines = [f"=== {self.name} ==="]
        lines.append(f"  Area:              {self.area_cm2_resolved:.4f} cm^2")
        lines.append(f"  Coating mass:      {self.coating_mass_g * 1000:.2f} mg  (AM: {self.am_mass_g * 1000:.2f} mg)")
        lines.append(f"  Coating thickness: {self.coating_thickness_mm * 1000:.1f} um")
        lines.append(f"  Coating density:   {self.coating_density_gcc:.3f} g/cc")
        lines.append(f"  Loading:           {self.loading_mg_cm2:.3f} mg/cm^2")
        lines.append(f"  Areal capacity:    {self.areal_capacity_mAh_cm2:.3f} mAh/cm^2")
        lines.append(f"  1C current:        {self.capacity_1C_mA:.4f} mA")
        lines.append("  C-rate currents:")
        for c, mA in self.currents_by_c_rate.items():
            lines.append(f"    {c:>6g}C : {mA:.4f} mA")
        return "\n".join(lines)
