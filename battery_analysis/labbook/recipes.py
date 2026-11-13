"""
Recipe calculators — turn "what I want" into "what to put on the balance".

Two kinds, both written as plain YAML inside a lab-book entry:

MixtureRecipe   (type: mixture)
    Everything by WEIGHT PERCENT OF THE FINAL TOTAL MASS — the same
    convention as "2.5, 5 and 7.5 wt% AdditiveA dispersed in 1 M LiPF6 in
    EC:EMC:DMC": the wt% is of the finished mixture (AdditiveA + electrolyte).

        type: mixture
        total_g: 5.0
        components:
          - {name: AdditiveA, wt_pct: 1}                       # dry powder
          - {name: E-0, balance: true}                     # whatever is left

    Each line is ONE THING YOU WEIGH. A line is either
      wt_pct: <n>    -> n wt% of the final mixture
      balance: true  -> the rest (exactly one line)
    and is either a pure material, or a stock solution (a name from
    materials.yaml `stocks:`, or a recipe id like E-0). To hit a target
    wt% of a SOLUTE that you add as a stock, say which stock delivers it:
          - {name: PVDF-HFP, wt_pct: 10, from: 15 wt% PVDF-HFP in NMP}
    and the calculator weighs out 10/15 of the stock instead, and takes
    the NMP it carries off the balance solvent automatically.

    Batch size: give total_g, OR basis: {<line name>: <grams>} to scale
    off one thing you already have (e.g. "I have 4.95 g of electrolyte").

MolarSolutionRecipe   (type: molar_solution)
    A salt at a molar concentration, e.g. 1 M NaPF6 in diglyme.

        type: molar_solution
        salt: NaPF6
        solvent: diglyme
        molarity_M: 1.0
        volume_mL: 10
        solution_density_g_mL: 1.09     # optional — see below

    Salt mass = c x V x M. Molarity is per litre of SOLUTION, so the
    honest instruction is "dissolve, then top up to the mark". If you
    know the solution's density (weigh 1.000 mL once), the calculator
    also gives the solvent mass, so you can make it on the balance and
    use it as a stock by weight in later mixtures.

Weighed values
    After weighing, write what the balance actually read under
    `weighed:` — the report then shows the ACTUAL composition next to
    the target, which is what goes into the paper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .materials import MaterialLibrary, Stock


class RecipeError(ValueError):
    pass


def _fmt_g(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.4f}"


# =========================================================
# MIXTURE (wt% of final total)
# =========================================================

@dataclass
class Line:
    name: str
    wt_pct: Optional[float] = None
    balance: bool = False
    source: Optional[str] = None       # `from:` — the stock that delivers this solute
    note: Optional[str] = None


@dataclass
class MixtureRecipe:
    id: str
    name: str
    lines: list
    total_g: Optional[float] = None
    basis: Optional[dict] = None
    weighed: dict = field(default_factory=dict)
    procedure: list = field(default_factory=list)
    notes: Optional[str] = None
    library: MaterialLibrary = field(default_factory=MaterialLibrary)
    stocks: dict = field(default_factory=dict)   # recipe ids / entry stocks usable as `from:`

    type = "mixture"

    # ----- what each line physically is -----

    def _stock_for(self, name: str) -> Optional[Stock]:
        return self.stocks.get(name) or self.library.stock(name)

    def _weighed_thing(self, line: Line) -> tuple[str, dict]:
        """(what goes on the balance, its composition as mass fractions)."""
        if line.source:
            stock = self._stock_for(line.source)
            if stock is None:
                raise RecipeError(f"{self.id}: `from: {line.source}` isn't a stock in materials.yaml or a recipe in this entry.")
            return stock.name, stock.composition
        stock = self._stock_for(line.name)
        if stock is not None:
            return stock.name, stock.composition
        name = self.library.canonical(line.name)
        return name, {name: 1.0}

    # ----- the maths: everything is linear in the total, so solve for 1 g -----

    def per_gram(self) -> list[float]:
        """Mass of each line to weigh per 1 g of final mixture."""
        balance = [ln for ln in self.lines if ln.balance]
        if len(balance) != 1:
            raise RecipeError(f"{self.id}: exactly one component needs `balance: true` (found {len(balance)}).")
        out = []
        for ln in self.lines:
            if ln.balance:
                out.append(None)
                continue
            if ln.wt_pct is None:
                raise RecipeError(f"{self.id}: {ln.name!r} needs wt_pct: (or balance: true).")
            w = ln.wt_pct / 100
            if ln.source:
                _, comp = self._weighed_thing(ln)
                species = self.library.canonical(ln.name)
                frac = comp.get(species) or comp.get(ln.name)
                if not frac:
                    raise RecipeError(f"{self.id}: stock {ln.source!r} contains no {ln.name!r}.")
                out.append(w / frac)
            else:
                out.append(w)
        rest = 1 - sum(x for x in out if x is not None)
        if rest < -1e-12:
            raise RecipeError(
                f"{self.id}: the components already add up to {100 * (1 - rest):.2f}% of the total "
                "before the balance — lower a wt%, or use a more concentrated stock."
            )
        return [rest if x is None else x for x in out]

    @property
    def total_mass_g(self) -> float:
        if (self.total_g is None) == (not self.basis):
            raise RecipeError(f"{self.id}: give exactly one of total_g: or basis: {{<component>: <grams>}}.")
        if self.total_g is not None:
            return float(self.total_g)
        (name, mass), = self.basis.items()
        per = self.per_gram()
        for ln, p in zip(self.lines, per):
            if ln.name == name:
                return float(mass) / p
        raise RecipeError(f"{self.id}: basis component {name!r} isn't one of the components.")

    def masses_g(self) -> list[float]:
        T = self.total_mass_g
        return [p * T for p in self.per_gram()]

    def composition(self, masses: list[float]) -> dict:
        """Final wt% of every species (stocks expanded into what they contain)."""
        total = sum(masses)
        species: dict = {}
        for ln, m in zip(self.lines, masses):
            _, comp = self._weighed_thing(ln)
            for s, frac in comp.items():
                species[s] = species.get(s, 0) + m * frac
        return {s: 100 * m / total for s, m in species.items()}

    def weighed_masses(self) -> Optional[list[float]]:
        if not self.weighed:
            return None
        missing = [ln.name for ln in self.lines if ln.name not in self.weighed]
        if missing:
            return None
        return [float(self.weighed[ln.name]) for ln in self.lines]

    def warnings(self) -> list[str]:
        out = []
        target = self.composition(self.masses_g())
        for ln in self.lines:
            if ln.balance or ln.wt_pct is None:
                continue
            species = ln.name if ln.source else self._weighed_thing(ln)[0]
            got = target.get(self.library.canonical(species), target.get(species))
            if got is not None and abs(got - ln.wt_pct) > 0.005 and not self._stock_for(ln.name):
                out.append(f"{ln.name}: asked for {ln.wt_pct:g} wt% but the mixture ends up at "
                           f"{got:.3f} wt% — another stock also contains it.")
        return out

    def result(self) -> dict:
        masses = self.masses_g()
        target = self.composition(masses)
        weighed = self.weighed_masses()
        actual = self.composition(weighed) if weighed else None
        rows = []
        for i, (ln, m) in enumerate(zip(self.lines, masses)):
            thing, comp = self._weighed_thing(ln)
            is_stock = len(comp) > 1 or thing != self.library.canonical(ln.name) or bool(self._stock_for(ln.name))
            rows.append({
                "step": i + 1,
                "component": ln.name,
                "weigh": thing,
                "form": "stock solution" if is_stock else "pure",
                "target_wt_pct": "balance" if ln.balance else f"{ln.wt_pct:g}",
                "mass_g": round(m, 6),
                "weighed_g": None if weighed is None else round(weighed[i], 6),
                "note": ln.note,
            })
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "total_g": round(sum(masses), 6),
            "rows": rows,
            "composition": [
                {"species": s, "target_wt_pct": round(v, 4),
                 "actual_wt_pct": None if actual is None else round(actual.get(s, 0), 4)}
                for s, v in target.items()
            ],
            "weighed_total_g": None if weighed is None else round(sum(weighed), 6),
            "procedure": self.procedure, "notes": self.notes,
            "warnings": self.warnings(),
        }

    def as_stock(self) -> Stock:
        """So a later mixture can use this recipe (e.g. an electrolyte) by name."""
        comp = {r["species"]: r["target_wt_pct"] / 100 for r in self.result()["composition"]}
        return Stock(name=self.id, composition=comp)

    def table(self) -> str:
        r = self.result()
        lines = [f"=== {r['id']}: {r['name']}  (total {r['total_g']:.4f} g) ==="]
        lines.append(f"  {'#':<3}{'Weigh out':<34}{'wt%':>9}{'Mass (g)':>12}{'Weighed':>11}")
        for row in r["rows"]:
            lines.append(f"  {row['step']:<3}{row['weigh'][:33]:<34}{row['target_wt_pct']:>9}"
                         f"{row['mass_g']:>12.4f}{_fmt_g(row['weighed_g']):>11}")
        lines.append("  Final composition (wt%):  " + ", ".join(
            f"{c['species']} {c['target_wt_pct']:.3f}" for c in r["composition"]))
        if r["weighed_total_g"] is not None:
            lines.append("  Actual (from weighed):    " + ", ".join(
                f"{c['species']} {c['actual_wt_pct']:.3f}" for c in r["composition"]))
        for w in r["warnings"]:
            lines.append(f"  !! {w}")
        return "\n".join(lines)


# =========================================================
# MOLAR SOLUTION (salt at c mol/L)
# =========================================================

@dataclass
class MolarSolutionRecipe:
    id: str
    name: str
    salt: str
    solvent: str
    molarity_M: float
    volume_mL: float
    solution_density_g_mL: Optional[float] = None
    weighed: dict = field(default_factory=dict)
    procedure: list = field(default_factory=list)
    notes: Optional[str] = None
    library: MaterialLibrary = field(default_factory=MaterialLibrary)

    type = "molar_solution"

    def _molar_mass(self) -> float:
        m = self.library.material(self.salt)
        if m is None or not m.molar_mass_g_mol:
            raise RecipeError(f"{self.id}: add {self.salt!r} with molar_mass_g_mol to LabBook/materials.yaml.")
        return m.molar_mass_g_mol

    @property
    def salt_g(self) -> float:
        return self.molarity_M * self.volume_mL / 1000 * self._molar_mass()

    @property
    def total_g(self) -> Optional[float]:
        if self.solution_density_g_mL is None:
            return None
        return self.solution_density_g_mL * self.volume_mL

    @property
    def solvent_g(self) -> Optional[float]:
        return None if self.total_g is None else self.total_g - self.salt_g

    def result(self) -> dict:
        salt = self.library.canonical(self.salt)
        solvent = self.library.canonical(self.solvent)
        mm = self._molar_mass()
        rows = [{
            "step": 1, "component": salt, "weigh": salt, "form": "pure",
            "target_wt_pct": f"{self.molarity_M:g} M", "mass_g": round(self.salt_g, 6),
            "weighed_g": self.weighed.get(self.salt, self.weighed.get(salt)),
            "note": f"{self.molarity_M:g} mol/L × {self.volume_mL:g} mL × {mm:g} g/mol",
        }, {
            "step": 2, "component": solvent, "weigh": solvent, "form": "pure",
            "target_wt_pct": "balance",
            "mass_g": None if self.solvent_g is None else round(self.solvent_g, 6),
            "weighed_g": self.weighed.get(self.solvent, self.weighed.get(solvent)),
            "note": (f"top up to {self.volume_mL:g} mL in a volumetric flask"
                     if self.solvent_g is None else
                     f"from solution density {self.solution_density_g_mL:g} g/mL"),
        }]
        comp = []
        if self.total_g:
            comp = [{"species": salt, "target_wt_pct": round(100 * self.salt_g / self.total_g, 4), "actual_wt_pct": None},
                    {"species": solvent, "target_wt_pct": round(100 * self.solvent_g / self.total_g, 4), "actual_wt_pct": None}]
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "total_g": None if self.total_g is None else round(self.total_g, 6),
            "volume_mL": self.volume_mL,
            "rows": rows, "composition": comp, "weighed_total_g": None,
            "procedure": self.procedure, "notes": self.notes, "warnings": [],
        }

    def as_stock(self) -> Stock:
        """Known density -> a weighable stock (salt + solvent); otherwise one opaque liquid."""
        if self.total_g is None:
            return Stock(name=self.id, composition={self.name: 1.0})
        salt = self.library.canonical(self.salt)
        return Stock(name=self.id, composition={
            salt: self.salt_g / self.total_g,
            self.library.canonical(self.solvent): self.solvent_g / self.total_g,
        })

    def table(self) -> str:
        r = self.result()
        lines = [f"=== {r['id']}: {r['name']}  ({self.volume_mL:g} mL) ==="]
        for row in r["rows"]:
            mass = "—" if row["mass_g"] is None else f"{row['mass_g']:.4f} g"
            lines.append(f"  {row['step']}. {row['weigh']:<16}{mass:>12}   {row['note']}")
        return "\n".join(lines)


# =========================================================
# Building recipes from an entry's YAML
# =========================================================

def _labelled(recipe) -> Stock:
    """The recipe as a stock, shown on the balance list as 'E-0 (1 M NaPF6 in G2)'."""
    s = recipe.as_stock()
    return Stock(name=f"{recipe.id} ({recipe.name})", composition=s.composition)


def build_recipes(specs: list, library: MaterialLibrary, external_stocks: Optional[dict] = None) -> list:
    """
    Turn an entry's `recipes:` list into recipe objects, in order, so a
    later recipe can use an earlier one (`from: E-0` or `name: E-0`).

    `vary:` makes a series in one go — like the paper's L-0/L-2.5/L-5/L-7.5:
        vary: {component: AdditiveA, wt_pct: [0, 2.5, 5, 7.5]}
    gives recipes <id>-0, <id>-2.5, ... ; their weighed masses go under
        weighed: {"<id>-2.5": {AdditiveA: 0.1251, E-0: 4.8760}, ...}
    """
    stocks = dict(external_stocks or {})
    out = []
    for spec in specs or []:
        spec = dict(spec)
        kind = spec.pop("type", "mixture")
        rid = str(spec.pop("id"))
        name = spec.pop("name", rid)
        if kind == "molar_solution":
            r = MolarSolutionRecipe(id=rid, name=name, library=library, **spec)
            out.append(r)
            stocks[rid] = _labelled(r)
            continue
        if kind != "mixture":
            raise RecipeError(f"{rid}: unknown recipe type {kind!r} (use mixture or molar_solution).")

        vary = spec.pop("vary", None)
        weighed = spec.pop("weighed", None) or {}
        components = spec.pop("components")

        def make(rid_, name_, comps, weighed_):
            lines = [Line(name=str(c["name"]), wt_pct=c.get("wt_pct"), balance=c.get("balance", False),
                          source=c.get("from"), note=c.get("note")) for c in comps]
            return MixtureRecipe(id=rid_, name=name_, lines=lines, weighed=weighed_,
                                 library=library, stocks=stocks, **spec)

        if vary:
            for v in vary["wt_pct"]:
                comps = [dict(c, wt_pct=v) if c["name"] == vary["component"] else c for c in components]
                sub_id = f"{rid}-{v:g}"
                r = make(sub_id, f"{name} — {v:g} wt% {vary['component']}", comps, weighed.get(sub_id, {}))
                out.append(r)
                stocks[sub_id] = _labelled(r)
        else:
            r = make(rid, name, components, weighed)
            out.append(r)
            stocks[rid] = _labelled(r)
    return out
