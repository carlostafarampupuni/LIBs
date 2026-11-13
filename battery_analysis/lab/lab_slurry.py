"""
Slurry formulation — turn a dry-weight ratio like "8:1:0.5:0.5" (AM :
Binder : Conductive additive : Additive) into actual masses to weigh
out, handling components that are delivered as a dilute stock solution
(PVDF in NMP, CMC in water) rather than dry powder, and reporting the
finished slurry's total solids content.

Ported from the battery_lab_toolkit's electrode_lab/slurry.py, folded
directly into battery_analysis (flat module, no new folder). Nothing
about the math changed — see test_lab_calc.py for the same numeric
checks the toolkit shipped with.

This generalizes "wt%-of-a-fixed-basis" recipe tables (e.g. a
"Super-P and MXene" / "CFAl" style table): those are the same math as a
ratio — a ratio is just wt% before you divide by its own sum.

Units: all masses in grams (g). Concentrations are fractions (0-1), not
percent — 10% PVDF-in-NMP stock is concentration_fraction=0.10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass
class SlurryComponent:
    """
    One ingredient in the dry recipe.

    ratio: its share of the DRY-weight ratio, e.g. 8 in "8:1:0.5:0.5".
        Ratios don't need to sum to anything in particular — they're
        normalized against each other automatically.
    concentration_fraction: leave at 1.0 for a component you weigh out
        as pure dry powder. Set it to e.g. 0.10 for "PVDF, added as a
        10% w/w solution in NMP" — the code then figures out how much
        of that stock solution to dispense to deliver the right amount
        of DRY binder, and how much solvent that stock brings with it.
    """
    name: str
    ratio: float
    concentration_fraction: float = 1.0
    is_solvent: bool = False   # a component you add ONLY to dilute (e.g. extra NMP) — no dry solids of its own

    def __post_init__(self):
        if self.ratio < 0:
            raise ValueError(f"{self.name}: ratio must be >= 0, got {self.ratio}.")
        if not (0 < self.concentration_fraction <= 1):
            raise ValueError(
                f"{self.name}: concentration_fraction must be in (0, 1], got "
                f"{self.concentration_fraction} — use 1.0 for a pure dry powder, or e.g. "
                f"0.10 for a 10% w/w stock solution."
            )


@dataclass
class SlurryFormulation:
    """
    A full recipe: a list of SlurryComponents plus a basis to scale them
    to an actual batch size.

    Give exactly one basis:
      basis_component_name + basis_mass_g   — "I want 4 g of LFP" — the
          rest of the recipe scales off that component's ratio.
      total_dry_solids_g                    — "I want 5 g of total dry
          solids across every non-solvent component" (this is how a
          fixed-basis wt% table works — its wt% columns are ratios that
          already sum to 1, scaled by a fixed total).

    extra_solvent_g: any additional pure solvent stirred in beyond what
      the stock solutions already carry (e.g. an "NMP" line in a
      formulation sheet). Optional — leave at 0 and read off
      total_solids_pct instead of trying to hit a target; or pass
      target_total_solids_pct instead of extra_solvent_g and the code
      solves for the solvent you need to add.
    """
    components: List[SlurryComponent]
    basis_component_name: Optional[str] = None
    basis_mass_g: Optional[float] = None
    total_dry_solids_g: Optional[float] = None
    extra_solvent_g: float = 0.0
    target_total_solids_pct: Optional[float] = None

    def __post_init__(self):
        has_component_basis = self.basis_component_name is not None and self.basis_mass_g is not None
        if has_component_basis == (self.total_dry_solids_g is not None):
            raise ValueError(
                "Give exactly one basis: (basis_component_name AND basis_mass_g) OR "
                "total_dry_solids_g — not both, not neither."
            )
        if has_component_basis and self.basis_component_name not in [c.name for c in self.components]:
            raise ValueError(f"basis_component_name {self.basis_component_name!r} isn't in components.")
        if self.extra_solvent_g and self.target_total_solids_pct:
            raise ValueError("Give extra_solvent_g OR target_total_solids_pct, not both.")

    @classmethod
    def from_ratio_string(cls, ratio: str, names: Sequence[str],
                           concentrations: Optional[Sequence[float]] = None, **kwargs) -> "SlurryFormulation":
        """
        SlurryFormulation.from_ratio_string(
            "8:1:0.5:0.5", ["LFP", "PVDF", "Super-P", "Additive"],
            concentrations=[1.0, 0.10, 1.0, 1.0],
            basis_component_name="LFP", basis_mass_g=4.0,
        )
        """
        parts = [float(p.strip()) for p in ratio.split(":")]
        if len(parts) != len(names):
            raise ValueError(f"Ratio {ratio!r} has {len(parts)} parts but {len(names)} names were given.")
        concentrations = concentrations or [1.0] * len(names)
        if len(concentrations) != len(names):
            raise ValueError("concentrations must have the same length as names.")
        components = [
            SlurryComponent(name=n, ratio=r, concentration_fraction=c)
            for n, r, c in zip(names, parts, concentrations)
        ]
        return cls(components=components, **kwargs)

    # ---------------------------------------------------------------

    @property
    def _solids_components(self) -> List[SlurryComponent]:
        return [c for c in self.components if not c.is_solvent]

    @property
    def _ratio_sum(self) -> float:
        return sum(c.ratio for c in self._solids_components)

    def dry_mass_g(self, component: SlurryComponent) -> float:
        if self.total_dry_solids_g is not None:
            basis_total = self.total_dry_solids_g
            return component.ratio / self._ratio_sum * basis_total
        basis_component = next(c for c in self.components if c.name == self.basis_component_name)
        return component.ratio / basis_component.ratio * self.basis_mass_g

    def dispensed_mass_g(self, component: SlurryComponent) -> float:
        """What you actually weigh out on the scale for this component (stock solution mass if diluted)."""
        return self.dry_mass_g(component) / component.concentration_fraction

    def solvent_from_stock_g(self, component: SlurryComponent) -> float:
        return self.dispensed_mass_g(component) - self.dry_mass_g(component)

    @property
    def total_dry_solids_mass_g(self) -> float:
        return sum(self.dry_mass_g(c) for c in self._solids_components)

    @property
    def total_dispensed_solids_mass_g(self) -> float:
        """Sum of what you weigh out for every non-solvent component (stock solutions included)."""
        return sum(self.dispensed_mass_g(c) for c in self._solids_components)

    @property
    def solvent_carried_by_stocks_g(self) -> float:
        return sum(self.solvent_from_stock_g(c) for c in self._solids_components)

    @property
    def resolved_extra_solvent_g(self) -> float:
        """
        The extra pure solvent to add, resolved from either
        extra_solvent_g (given directly) or target_total_solids_pct
        (back-solved so the finished slurry hits that solids %).
        """
        if self.target_total_solids_pct is None:
            return self.extra_solvent_g
        target_frac = self.target_total_solids_pct / 100
        if not (0 < target_frac < 1):
            raise ValueError(f"target_total_solids_pct must be between 0 and 100, got {self.target_total_solids_pct}.")
        total_slurry_mass_target = self.total_dry_solids_mass_g / target_frac
        extra = total_slurry_mass_target - self.total_dispensed_solids_mass_g
        if extra < 0:
            raise ValueError(
                f"The stock solutions alone already dilute this recipe below "
                f"{self.target_total_solids_pct}% solids ({self.total_dry_solids_mass_g / self.total_dispensed_solids_mass_g * 100:.2f}% "
                f"as mixed) — target_total_solids_pct can't be reached by ADDING more solvent. "
                f"Use a more concentrated stock, or raise the target."
            )
        return extra

    @property
    def total_solvent_mass_g(self) -> float:
        return self.solvent_carried_by_stocks_g + self.resolved_extra_solvent_g

    @property
    def total_slurry_mass_g(self) -> float:
        return self.total_dry_solids_mass_g + self.total_solvent_mass_g

    @property
    def total_solids_pct(self) -> float:
        return self.total_dry_solids_mass_g / self.total_slurry_mass_g * 100

    def summary(self) -> dict:
        rows = []
        for c in self.components:
            if c.is_solvent:
                continue
            rows.append({
                "component": c.name,
                "ratio": c.ratio,
                "concentration_pct": round(c.concentration_fraction * 100, 4),
                "dry_mass_g": round(self.dry_mass_g(c), 6),
                "dispense_mass_g": round(self.dispensed_mass_g(c), 6),
            })
        return {
            "components": rows,
            "extra_solvent_g": round(self.resolved_extra_solvent_g, 6),
            "total_dry_solids_g": round(self.total_dry_solids_mass_g, 6),
            "total_slurry_mass_g": round(self.total_slurry_mass_g, 6),
            "total_solids_pct": round(self.total_solids_pct, 4),
        }

    def report(self) -> str:
        s = self.summary()
        lines = ["=== Slurry formulation ==="]
        lines.append(f"{'Component':<16}{'Ratio':>8}{'Conc %':>10}{'Dry (g)':>12}{'Dispense (g)':>15}")
        for r in s["components"]:
            lines.append(
                f"{r['component']:<16}{r['ratio']:>8g}{r['concentration_pct']:>10g}"
                f"{r['dry_mass_g']:>12.4f}{r['dispense_mass_g']:>15.4f}"
            )
        lines.append(f"{'Extra solvent':<16}{'':>8}{'':>10}{'':>12}{s['extra_solvent_g']:>15.4f}")
        lines.append("")
        lines.append(f"  Total dry solids:  {s['total_dry_solids_g']:.4f} g")
        lines.append(f"  Total slurry mass: {s['total_slurry_mass_g']:.4f} g")
        lines.append(f"  Total solids:      {s['total_solids_pct']:.2f} %")
        return "\n".join(lines)
