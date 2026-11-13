"""
Any CSV/Excel time series whose columns you map by hand in datasets.yaml,
e.g. an Arbin, Maccor or BioLogic export that is not BDF yet:

    reader: csv_map
    options:
      sep: ","                 # optional
      sheet: 0                 # Excel only
      columns:                 # source header -> BDF key (units in the key are converted)
        "Test_Time(s)": test_time_second
        "Voltage(V)": voltage_volt
        "Current(A)": current_ampere
        "Cycle_Index": cycle_count
      current_sign: 1          # -1 if the file uses negative = charge
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import CellData, infer_form_factor, register


@register("csv_map")
def read(path: Path, options: dict) -> list[CellData]:
    mapping = options.get("columns") or {}
    if not mapping:
        raise ValueError("csv_map needs options.columns: {source header: BDF key}")
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, sheet_name=options.get("sheet", 0))
    else:
        raw = pd.read_csv(path, sep=options.get("sep", ","), engine="pyarrow" if options.get("sep", ",") == "," else None)
    missing = [c for c in mapping if c not in raw.columns]
    if missing:
        raise ValueError(f"{path.name}: columns {missing} not found; file has {list(raw.columns)}")
    df = raw[list(mapping)].rename(columns=mapping)
    if options.get("current_sign", 1) == -1:
        cur = [c for c in df.columns if c.startswith("current_")]
        df[cur] = -df[cur]
    return [CellData(cell_id=path.stem, meta={"form_factor": infer_form_factor(path.stem)}, timeseries=df)]
