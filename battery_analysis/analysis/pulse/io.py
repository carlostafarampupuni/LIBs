"""Reading galvanostatic pulse files (Ivium/IviumSoft text exports and similar tables)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

_CURRENT_UNITS = {"a": 1.0, "ma": 1e-3, "ua": 1e-6, "µa": 1e-6, "μa": 1e-6, "na": 1e-9, "pa": 1e-12}
_VOLT_UNITS = {"v": 1.0, "mv": 1e-3}
_TIME_UNITS = {"s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0}


def _unit(header: str) -> str:
    """'I /nA' -> 'na', 'Current(mA)' -> 'ma', 'E [V]' -> 'v'."""
    m = re.search(r"[/(\[]\s*([A-Za-zµμ]+)\s*[)\]]?\s*$", header.strip())
    return m.group(1).lower() if m else ""


def read_pulse_file(path) -> pd.DataFrame:
    """
    One pulse/cycling text file -> DataFrame with t_s, I_A, E_V (SI units).
    Columns are recognised by meaning and their unit is read from the header
    ('time /s', 'I /nA', 'I /mA', 'E /V', 'Voltage (mV)', ...). Files with no
    recognisable header are read as time, current, potential in that order,
    in s, A and V.
    """
    path = Path(path)
    df = pd.read_csv(path, sep=None, engine="python", encoding="latin-1")
    cols = [str(c).strip() for c in df.columns]
    low = [c.lower() for c in cols]

    def pick(*keys):
        return next((i for i, c in enumerate(low) if any(re.match(k, c) for k in keys)), None)

    it = pick(r"time", r"t\b", r"t\s*/")
    ii = pick(r"i\s*[/(\[]", r"i$", r"current")
    ie = pick(r"e\s*[/(\[]", r"e$", r"ewe", r"potential", r"voltage", r"v\s*[/(\[]")
    if None in (it, ii, ie):
        it, ii, ie = 0, 1, 2
        ft = fi = fe = 1.0
    else:
        ft = _TIME_UNITS.get(_unit(cols[it]), 1.0)
        fi = _CURRENT_UNITS.get(_unit(cols[ii]), 1.0)
        fe = _VOLT_UNITS.get(_unit(cols[ie]), 1.0)
    out = pd.DataFrame({
        "t_s": pd.to_numeric(df.iloc[:, it], errors="coerce") * ft,
        "I_A": pd.to_numeric(df.iloc[:, ii], errors="coerce") * fi,
        "E_V": pd.to_numeric(df.iloc[:, ie], errors="coerce") * fe,
    }).dropna().reset_index(drop=True)
    out.attrs["source"] = str(path)
    out.attrs["current_unit"] = _unit(cols[ii]) if ii is not None else "a"
    return out


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def pulse_files(folder, pattern: str = "*.txt") -> List[Path]:
    """Files in a folder in natural order (defaultdata_2 before defaultdata_10)."""
    folder = Path(folder)
    return sorted((p for p in folder.glob(pattern) if p.is_file() and not p.name.startswith(("~$", "."))),
                  key=_natural_key)
