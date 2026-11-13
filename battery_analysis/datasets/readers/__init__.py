"""
Readers: one function per file format, all returning the same thing.

    @register("myformat")
    def read(path: Path, options: dict) -> list[CellData]: ...

A reader only converts. Cleaning, cycle summaries, storage and plots happen
after it, the same for every format. To support a new dataset, write one
reader (often 20–40 lines) and add the dataset to datasets.yaml.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

READERS: dict[str, Callable] = {}


@dataclass
class CellData:
    cell_id: str
    meta: dict = field(default_factory=dict)
    timeseries: Optional[pd.DataFrame] = None     # BDF columns
    eis: Optional[pd.DataFrame] = None            # cycle, frequency_hertz, z_real_ohm, z_imag_ohm, ...
    extra: dict = field(default_factory=dict)     # detailed metadata (flat key -> value), stored per cell


def register(name: str):
    def deco(fn):
        READERS[name] = fn
        return fn
    return deco


def get_reader(name: str) -> Callable:
    if not READERS:
        _load_all()
    if name not in READERS:
        _load_all()
    if name not in READERS:
        raise KeyError(f"no reader {name!r}; available: {sorted(READERS)}")
    return READERS[name]


def _load_all():
    from . import bdf, csv_map, neware, zhu2022_eis  # noqa: F401


# ---- shared helper: guess the cell format from a cell name -----------------
_FORM = [
    (r"(?<!\d)(18650)(?!\d)", "cylindrical 18650"), (r"(?<!\d)(21700)(?!\d)", "cylindrical 21700"),
    (r"(?<!\d)(4680)(?!\d)", "cylindrical 4680"), (r"(?<!\d)(26650)(?!\d)", "cylindrical 26650"),
    (r"(?<!\d)32140(?!\d)", "cylindrical 32140"),
    (r"C?R20(16|25|32)", "coin"), (r"coin", "coin"),
    (r"pouch|SLPB|Kokam", "pouch"), (r"prismatic", "prismatic"),
]


def infer_form_factor(name: str) -> str:
    for pat, form in _FORM:
        if re.search(pat, name, flags=re.I):
            return form
    return "unknown"
