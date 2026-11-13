"""
Synthetic-file checks for neware_io.py (no real NEWARE file yet). Run with

    python -m battery_analysis.analysis.cycling_common.test_neware_io

Each fake cycle: rest, CC charge to 1.0 mAh, CV charge +0.2 mAh, rest,
CC discharge 1.1 mAh  ->  charge 1.2 mAh, discharge 1.1 mAh, CE 91.67 %.
"""
import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from battery_analysis.analysis.cycling_common.neware_io import NewareParser, cycle_series_from_neware


def _fake_records(n_cycles=3):
    rows, step = [], 0
    for cyc in range(1, n_cycles + 1):
        for kind, cap_end, current, v0, v1 in [("Rest", 0, 0, 3.0, 3.0), ("CC Chg", 1.0, 0.5, 3.0, 4.2),
                                               ("CV Chg", 0.2, 0.1, 4.2, 4.2), ("Rest", 0, 0, 4.1, 4.1),
                                               ("CC DChg", 1.1, -0.5, 4.1, 2.5)]:
            step += 1
            for k in range(5):
                f = k / 4
                rows.append({"Cycle Index": cyc, "Step Index": step, "Step Type": kind,
                             "Total Time": f"{step}:{k:02d}:00.000",
                             "Voltage(V)": v0 + (v1 - v0) * f,
                             "Current(A)": current / 1000,                 # A, to test unit conversion
                             "Capacity(Ah)": cap_end * f / 1000})
    return pd.DataFrame(rows)


def _check(parser):
    s = parser.cycle_summary()
    assert list(s["Cycle"]) == [1, 2, 3]
    # active mass 0.01 g -> 1.2 mAh = 120 mAh/g
    assert np.allclose(s["Q_Charge"], 120) and np.allclose(s["Q_Discharge"], 110)
    assert math.isclose(s["CE (%)"].iloc[0], 100 * 1.1 / 1.2)
    q_ch, v_ch, q_dis, v_dis = parser.voltage_profile(2)
    assert math.isclose(q_ch.max(), 120) and math.isclose(v_dis.min(), 2.5)
    assert cycle_series_from_neware(parser, "Bare").capacity_discharge[0] == s["Q_Discharge"].iloc[0]


def test_xlsx_export():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "CH1-3_Bare.xlsx"
        with pd.ExcelWriter(p) as w:
            pd.DataFrame({"Cycle Index": [1]}).to_excel(w, sheet_name="cycle", index=False)
            _fake_records().to_excel(w, sheet_name="record", index=False)
        _check(NewareParser(p, active_mass_g=0.01))


def test_csv_export_chinese_headers():
    with tempfile.TemporaryDirectory() as d:
        df = _fake_records().rename(columns={"Cycle Index": "循环号", "Step Index": "工步号", "Step Type": "工步类型",
                                             "Voltage(V)": "电压(V)", "Current(A)": "电流(A)", "Capacity(Ah)": "容量(Ah)"})
        df["工步类型"] = df["工步类型"].map({"Rest": "搁置", "CC Chg": "恒流充电", "CV Chg": "恒压充电", "CC DChg": "恒流放电"})
        p = Path(d) / "CH1-3.csv"
        df.to_csv(p, index=False, encoding="gbk")
        _check(NewareParser(p, active_mass_g=0.01))


def test_plating_stripping_ce():
    # Na||Cu style: CE = stripped (charge) / plated (discharge) = 1.2 / 1.1
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "NaCu.csv"
        _fake_records().to_csv(p, index=False)
        s = NewareParser(p, ce="charge/discharge").cycle_summary()
        assert math.isclose(s["CE (%)"].iloc[0], 100 * 1.2 / 1.1)
        assert math.isclose(s["Q_Charge"].iloc[0], 1.2)     # no mass -> plain mAh


def test_real_btsda_layout_rest_only():
    # Same sheets/headers as the lab's 2026-10-22 exports (unit/test/cycle/step/record),
    # a test stopped during the first rest: parses, but has no cycles yet.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "0%_AdditiveA_Cu1.xlsx"
        rec = pd.DataFrame({"DataPoint": [1, 2], "Cycle Index": [1, 1], "Step Index": [1, 1],
                            "Step Type": ["Rest", "Rest"], "Time": ["00:00:00", "00:00:30"],
                            "Total Time": ["00:00:00", "00:00:30"], "Current(A)": [0, 0],
                            "Voltage(V)": [2.372, 2.371], "Capacity(Ah)": [0, 0], "Spec. Cap.(mAh/g)": [0, 0],
                            "Chg. Cap.(Ah)": [0, 0], "DChg. Cap.(Ah)": [0, 0],
                            "Date": ["2026-10-22 11:35:16", "2026-10-22 11:35:46"]})
        with pd.ExcelWriter(p) as w:
            for name in ("unit", "test", "cycle", "step"):
                pd.DataFrame({"x": [1]}).to_excel(w, sheet_name=name, index=False)
            rec.to_excel(w, sheet_name="record", index=False)
        parser = NewareParser(p, ce="charge/discharge")
        assert len(parser.records) == 2 and parser.records["time_s"].iloc[-1] == 30
        assert parser.cycle_summary().empty


if __name__ == "__main__":
    for name, f in [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]:
        f()
        print(f"ok  {name}")
