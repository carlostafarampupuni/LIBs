"""
Materials and stock solutions — the shelf of things you weigh out.

LabBook/materials.yaml lists every chemical once (molar mass, density,
supplier, aliases) and every stock solution you keep on the shelf (e.g.
"8 wt% PVDF in NMP", made once and used for weeks). Recipes then refer
to them by name, so the calculator always knows that "G2" is diglyme,
134.17 g/mol, 0.937 g/mL, without you typing it again.

    materials:
      - name: diglyme
        aliases: [G2, DEGDME, DMG]
        formula: C6H14O3
        molar_mass_g_mol: 134.17
        density_g_mL: 0.937

    stocks:
      - name: 8 wt% PVDF in NMP
        composition: {PVDF: 8}        # wt% of each solute
        balance: NMP                  # whatever is left
        made_in: 2026-09-10a          # optional: the entry where you made it
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Material:
    name: str
    formula: Optional[str] = None
    molar_mass_g_mol: Optional[float] = None
    density_g_mL: Optional[float] = None
    supplier: Optional[str] = None
    purity: Optional[str] = None
    cas: Optional[str] = None
    notes: Optional[str] = None
    aliases: list = field(default_factory=list)


@dataclass
class Stock:
    """
    A solution you weigh out as ONE thing but that delivers several
    species. composition holds mass FRACTIONS (0-1) of every species,
    summing to 1 — e.g. {"PVDF": 0.08, "NMP": 0.92}.
    """
    name: str
    composition: dict
    made_in: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_spec(cls, spec: dict) -> "Stock":
        solutes = {k: float(v) / 100 for k, v in (spec.get("composition") or {}).items()}
        total = sum(solutes.values())
        if total > 1 + 1e-9:
            raise ValueError(f"Stock {spec['name']!r}: solutes add up to {total * 100:g} wt% (> 100).")
        balance = spec.get("balance")
        if balance:
            solutes[balance] = solutes.get(balance, 0) + (1 - total)
        elif abs(total - 1) > 1e-6:
            raise ValueError(
                f"Stock {spec['name']!r}: composition adds up to {total * 100:g} wt% — "
                "add `balance: <solvent>` for whatever makes up the rest."
            )
        return cls(name=spec["name"], composition=solutes,
                   made_in=spec.get("made_in"), notes=spec.get("notes"))

    def describe(self) -> str:
        return ", ".join(f"{k} {v * 100:g}%" for k, v in self.composition.items())


class MaterialLibrary:
    """Everything in materials.yaml, looked up by name or alias (case-insensitive)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.materials: dict[str, Material] = {}
        self.stocks: dict[str, Stock] = {}
        self._alias: dict[str, str] = {}
        if path is not None and Path(path).exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            for spec in raw.get("materials") or []:
                self.add_material(Material(**spec))
            for spec in raw.get("stocks") or []:
                self.add_stock(Stock.from_spec(spec))

    def add_material(self, m: Material) -> None:
        self.materials[m.name] = m
        for key in [m.name, *m.aliases]:
            self._alias[str(key).lower()] = m.name

    def add_stock(self, s: Stock) -> None:
        self.stocks[s.name] = s

    def material(self, name: str) -> Optional[Material]:
        key = self._alias.get(str(name).lower())
        return self.materials.get(key) if key else None

    def canonical(self, name: str) -> str:
        """'G2' -> 'diglyme'; unknown names come back unchanged."""
        m = self.material(name)
        return m.name if m else name

    def stock(self, name: str) -> Optional[Stock]:
        for key, s in self.stocks.items():
            if key.lower() == str(name).lower():
                return s
        return None
