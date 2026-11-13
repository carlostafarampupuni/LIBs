"""
EIS of commercial 18650 cells during cycling aging:
Zhu et al., "Data-driven capacity estimation of commercial lithium-ion
batteries from voltage relaxation", Nat. Commun. 2022; Zenodo 6405084,
file "Impedance raw data and fitting data.zip" (CC BY 4.0).

Layout: <chemistry> battery/CY<T>_<C-rate>_<n>.xlsx, one sheet per check-up
named "<X>_1RCPE" or "<X>_2RCPE"; columns "Data: Frequency/Z'/Z''" and
"Fit: ...". From the dataset's own description: cycle = (X−1)·25 for NCA,
(X−1)·50 for NCM and NCM+NCA. One file = one cell. Z'' is stored with the
sign the file has (negative = capacitive).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import CellData, register

_STEP = {"NCA": 25, "NCM": 50, "NCM+NCA": 50}


@register("zhu2022_eis")
def read(path: Path, options: dict) -> list[CellData]:
    chem = path.parent.name.replace(" battery", "").strip()
    m = re.match(r"CY(\d+)_([\d.]+)_(\d+)", path.stem)
    meta = {"chemistry": chem, "form_factor": "cylindrical 18650", "condition": path.stem,
            "test_type": "EIS during cycling aging"}
    if m:
        meta.update(test_temperature=f"{m.group(1)}degC", c_rate=float(m.group(2)))
    step = _STEP.get(chem, 50)
    parts = []
    with pd.ExcelFile(path, engine="openpyxl") as xl:
        for sheet in xl.sheet_names:
            x = int(sheet.split("_")[0])
            # Read by POSITION: some sheets have the "Data: Frequency ..." header row,
            # some have none, and some carry an instrument note row ("...mpr", "DC=4.17, ...")
            # under the header. Non-numeric rows are dropped; the note is kept.
            raw = pd.read_excel(xl, sheet_name=sheet, header=None).iloc[:, :6]
            num = raw.apply(pd.to_numeric, errors="coerce")
            text = raw[num.iloc[:, 0].isna() & raw.iloc[:, 0].notna()]
            note = " ".join(str(v) for v in text.iloc[-1].dropna()) if len(text) and \
                not str(text.iloc[-1, 0]).startswith("Data:") else ""
            d = num.dropna(subset=[0, 1, 2])
            parts.append(pd.DataFrame({
                "cycle": (x - 1) * step, "checkup": x,
                "frequency_hertz": d[0], "z_real_ohm": d[1], "z_imag_ohm": d[2],
                "z_real_fit_ohm": d[4] if 4 in d else None, "z_imag_fit_ohm": d[5] if 5 in d else None,
                "fit_model": sheet.split("_", 1)[1] if "_" in sheet else "",
                "note": note,
            }))
    return [CellData(cell_id=f"{chem}_{path.stem}", meta=meta, eis=pd.concat(parts, ignore_index=True))]
