"""
One lab-book entry = one YAML file in LabBook/entries/, named by its id:

    LabBook/entries/2026-10-22a_AdditiveA-NaPF6-G2.yaml
                    └── id ──┘ └── anything readable ──┘

The id is the date you STARTED the experiment plus a letter (a, b, c …
for a second or third experiment begun the same day). Its photos live
in Results/Figures/Photos/<id>/.

Every section is optional — write what you did, skip what you didn't:

    id, title, date, operator, status, tags, aim
    conditions:      {glovebox: Ar, H2O_ppm: 0.1, ...}   any key: value
    materials_used:  [{name, supplier, lot, purity}]     what came off the shelf
    stocks:          stock solutions defined just for this entry
    recipes:         see recipes.py (mixture / molar_solution)
    electrode_defaults + electrodes + np_pairs           see lab_electrode.py
    cells:           [{id, cathode, anode, electrolyte, ...}]
    log:             [{time, text}]                       timestamped notes
    notes, results, next_steps                            free text

Free text is Typst markup, so *bold*, _italic_, $"P"_2"O"_5$ and
photos all work:

    #photo("dissolved.jpg", caption: [Clear after 12 h], width: 60%)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from battery_analysis.lab.lab_electrode import ElectrodeMeasurement
from battery_analysis.lab.lab_np_ratio import np_ratio_from_electrodes

from .materials import MaterialLibrary, Stock
from .recipes import build_recipes

ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})([a-z])$")
_ELECTRODE_FIELDS = set(ElectrodeMeasurement.__dataclass_fields__)


@dataclass
class LabEntry:
    path: Path
    raw: dict
    library: MaterialLibrary
    book: Optional[object] = None          # the LabBook, for cross-entry references
    _recipes: Optional[list] = field(default=None, repr=False)
    _electrodes: Optional[dict] = field(default=None, repr=False)

    @classmethod
    def load(cls, path: Path, library: MaterialLibrary, book=None) -> "LabEntry":
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        raw["id"] = str(raw.get("id") or path.stem.split("_")[0])
        if "date" in raw:
            raw["date"] = str(raw["date"])
        return cls(path=path, raw=raw, library=library, book=book)

    # ----- plain fields -----

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def title(self) -> str:
        return self.raw.get("title", self.id)

    @property
    def date(self) -> str:
        if "date" in self.raw:
            return self.raw["date"]
        m = ID_RE.match(self.id)
        return m.group(1) if m else ""

    # ----- calculated sections -----

    def _local_stocks(self) -> dict:
        return {s["name"]: Stock.from_spec(s) for s in self.raw.get("stocks") or []}

    def _external_stocks(self) -> dict:
        """`from: 2026-10-20a/E-0` -> that entry's recipe E-0, usable here as a stock."""
        out = self._local_stocks()
        if self.book is None:
            return out
        for spec in self.raw.get("recipes") or []:
            for c in spec.get("components") or []:
                for ref in (c.get("name"), c.get("from")):
                    if isinstance(ref, str) and "/" in ref and ID_RE.match(ref.split("/")[0]):
                        entry_id, rid = ref.split("/", 1)
                        other = self.book.get(entry_id)
                        match = [r for r in other.recipes if r.id == rid]
                        if not match:
                            raise KeyError(f"{self.id}: {ref!r} — entry {entry_id} has no recipe {rid!r}.")
                        stock = match[0].as_stock()
                        out[ref] = Stock(name=ref, composition=stock.composition)
        return out

    @property
    def recipes(self) -> list:
        if self._recipes is None:
            self._recipes = build_recipes(self.raw.get("recipes"), self.library, self._external_stocks())
        return self._recipes

    @property
    def electrodes(self) -> dict:
        """name -> (ElectrodeMeasurement, the extra keys you wrote, e.g. sample/notes)."""
        if self._electrodes is None:
            defaults = self.raw.get("electrode_defaults") or {}
            self._electrodes = {}
            for spec in self.raw.get("electrodes") or []:
                spec = {**defaults, **spec}
                extra = {k: v for k, v in spec.items() if k not in _ELECTRODE_FIELDS}
                e = ElectrodeMeasurement(**{k: v for k, v in spec.items() if k in _ELECTRODE_FIELDS})
                self._electrodes[e.name] = (e, extra)
        return self._electrodes

    def np_ratios(self) -> list:
        rows = []
        for pair in self.raw.get("np_pairs") or []:
            a, c = self.electrodes.get(pair["anode"]), self.electrodes.get(pair["cathode"])
            if a is None or c is None:
                raise KeyError(f"{self.id}: N/P pair {pair} names an electrode not in `electrodes:`.")
            rows.append(np_ratio_from_electrodes(a[0], c[0]))
        return rows

    def lookup(self, what: str):
        """'P1-C2' -> am_mass_g; 'P1-C2.loading_mg_cm2' -> that value; 'L-1.total_g' for a recipe."""
        name, _, fld = what.partition(".")
        if name in self.electrodes:
            e = self.electrodes[name][0]
            summary = e.summary()
            fld = fld or "am_mass_g"
            if fld not in summary:
                raise KeyError(f"{self.id}/{name}: no field {fld!r}. Choose from {sorted(summary)}.")
            return summary[fld]
        for r in self.recipes:
            if r.id == name:
                res = r.result()
                if fld in res:
                    return res[fld]
                raise KeyError(f"{self.id}/{name}: no field {fld!r}.")
        raise KeyError(f"{self.id}: nothing called {name!r} (electrodes: {list(self.electrodes)}).")

    # ----- for printing -----

    def calc_text(self) -> str:
        parts = [f"{self.id} — {self.title}"]
        parts += [r.table() for r in self.recipes]
        for e, _ in self.electrodes.values():
            parts.append(e.report())
        for np_row in self.np_ratios():
            parts.append(f"N/P {np_row['anode']} / {np_row['cathode']} = {np_row['np_ratio']:.3f}"
                         + (f"   !! {np_row['warning']}" if np_row["warning"] else ""))
        return "\n\n".join(parts)

    def materials_table(self) -> list:
        rows = []
        for m in self.raw.get("materials_used") or []:
            m = dict(m)
            lib = self.library.material(m["name"])
            if lib:
                m.setdefault("formula", lib.formula)
                m.setdefault("molar_mass_g_mol", lib.molar_mass_g_mol)
                m.setdefault("density_g_mL", lib.density_g_mL)
                m.setdefault("supplier", lib.supplier)
                m.setdefault("purity", lib.purity)
            rows.append(m)
        return rows

    def data(self) -> dict:
        """Everything the PDF report shows, as plain JSON-able data."""
        r = self.raw
        electrodes = []
        for e, extra in self.electrodes.values():
            s = e.summary()
            electrodes.append({**{k: v for k, v in extra.items() if not isinstance(v, (dict, list))}, **s,
                               "c_rates": {f"{c:g}C": round(e.current_at_c_rate(c), 4) for c in e.c_rates}})
        return {
            "id": self.id, "title": self.title, "date": self.date,
            "operator": r.get("operator"), "status": r.get("status"),
            "tags": r.get("tags") or [],
            # blanks you haven't filled in yet are left out of the PDF
            "conditions": {str(k): str(v) for k, v in (r.get("conditions") or {}).items() if v not in (None, "")},
            "materials": [{k: ("" if v is None else str(v)) for k, v in m.items()} for m in self.materials_table()],
            "stocks": [{"name": s.name, "composition": s.describe(), "made_in": s.made_in or ""}
                       for s in self._local_stocks().values()],
            "recipes": [x.result() for x in self.recipes],
            "electrodes": electrodes,
            "np_ratios": self.np_ratios(),
            "cells": [{k: str(v) for k, v in c.items()} for c in r.get("cells") or []],
            "log": [{"time": str(x.get("time") or ""), "text": str(x.get("text") or "")}
                    for x in r.get("log") or [] if x.get("time") or x.get("text")],
            "notes": r.get("notes") or None, "results": r.get("results") or None,
            "next_steps": r.get("next_steps") or None, "aim": r.get("aim") or None,
        }
