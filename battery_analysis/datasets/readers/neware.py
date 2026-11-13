"""
Your own NEWARE files (.ndax, .nda, BTSDA .xlsx/.csv) into the same store,
through the parser the GCD/cycle modules already use. Lets your pouch cells
sit next to public data and go through the same cycle summary and features.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import CellData, infer_form_factor, register


@register("neware")
def read(path: Path, options: dict) -> list[CellData]:
    from battery_analysis.analysis.cycling_common.neware_io import normalize, read_records
    df = normalize(read_records(path))
    ts = pd.DataFrame({
        "test_time_second": df["time_s"] if "time_s" in df else pd.Series(range(len(df)), dtype=float),
        "voltage_volt": df["voltage_V"],
        "current_ampere": df["current_mA"] / 1000.0,
        "cycle_count": df["cycle"],
        "step_count": df["step"],
    })
    return [CellData(cell_id=path.stem, meta={"form_factor": options.get("form_factor", infer_form_factor(path.stem)),
                                              "cycler": "NEWARE"}, timeseries=ts)]
