"""
Self-tests on synthetic cells with KNOWN answers (no network needed).
Run:  python -m battery_analysis.datasets.test_datasets   (or pytest)
"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from battery_analysis.datasets import DatasetStore, ingest, load_spec
from battery_analysis.datasets.cycles import summarize
from battery_analysis.datasets.features import cell_features
from battery_analysis.datasets.schema import clean, normalize


def synthetic_cell(n_cycles=5, cap_ah=2.0, current=1.0, fade=0.01, dt=3.6, signed=True, ms=False):
    """CC charge / rest / CC discharge cycles; discharge capacity fades by `fade` per cycle."""
    rows, t = [], 0.0
    for c in range(1, n_cycles + 1):
        q = cap_ah * (1 - fade * (c - 1))
        for sign, dur in ((1, q / current * 3600), (0, 600.0), (-1, q / current * 3600 * 0.995)):
            n = int(round(dur / dt))
            for k in range(1, n + 1):
                v = 3.0 + 1.0 * k / n if sign > 0 else (4.0 - 1.0 * k / n if sign < 0 else 3.9)
                rows.append((t + k * dt, v, sign * current if signed else abs(sign) * current, c))
            t += n * dt
    df = pd.DataFrame(rows, columns=["test_time_second", "voltage_volt", "current_ampere", "cycle_count"])
    if ms:
        df = df.rename(columns={"test_time_second": "test_time_millisecond", "cycle_count": "cycle_dimensionless"})
        df["test_time_millisecond"] *= 1000
    return df


def test_units_and_sentinels():
    df = synthetic_cell(2, ms=True)
    df["ambient_temperature_celsius"] = -9999.0
    ts, issues = clean(normalize(df))
    assert "test_time_second" in ts and "cycle_count" in ts
    assert ts["test_time_second"].iloc[-1] < 1e6                       # ms -> s
    assert "ambient_temperature_celsius" not in ts                      # all-sentinel column dropped
    assert any("sentinel" in i for i in issues)


def test_integrated_capacity_is_exact():
    cyc = summarize(clean(normalize(synthetic_cell(5)))[0])
    expect = 2.0 * (1 - 0.01 * np.arange(5))
    assert np.allclose(cyc["charge_ah"], expect, rtol=1e-3)
    assert np.allclose(cyc["discharge_ah"], expect * 0.995, rtol=1e-3)
    assert np.allclose(cyc["ce"], 0.995, rtol=1e-3)


def test_unsigned_current_is_repaired():
    ts, issues = clean(normalize(synthetic_cell(3, signed=False)))
    assert (ts["current_ampere"] < 0).any() and any("no sign" in i for i in issues)
    cyc = summarize(ts)
    assert np.allclose(cyc["ce"], 0.995, rtol=1e-3)


def test_cycles_without_cycle_column():
    df = synthetic_cell(4).drop(columns="cycle_count")
    assert len(summarize(clean(normalize(df))[0])) == 4


def test_features():
    f = cell_features(summarize(clean(normalize(synthetic_cell(30, fade=0.01)))[0]), ref_cycles=1)
    assert f["eol_cycle"] == 22                              # 1 − 0.01·(c−1) < 0.8 first at c = 22
    assert abs(f["fade_pct_per_100"] - 100.0) < 1.0          # 1 %/cycle = 100 %/100 cycles


def test_pipeline_incremental_and_parallel():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        raw = proj / "Data/Public/raw/demo"
        raw.mkdir(parents=True)
        for k in range(3):
            synthetic_cell(3 + k).to_csv(raw / f"lab__pouch{k}__2026__cycling__25degC__X.bdf.csv", index=False)
        (raw / "broken.bdf.csv").write_text("a,b\n1,2\n")
        (proj / "notebooks").mkdir()
        (proj / "notebooks/datasets.yaml").write_text(
            "datasets:\n  demo:\n    reader: bdf\n    raw: demo\n    files: '*.bdf.csv'\n    meta: {form_factor: pouch}\n")
        spec = load_spec(proj / "notebooks/datasets.yaml")
        rep = ingest(spec, workers=1)
        assert (rep["status"] == "ok").sum() == 3 and (rep["status"] == "error").sum() == 1
        store = DatasetStore(spec["_store"])
        assert len(store.catalog()) == 3 and set(store.catalog()["form_factor"]) == {"pouch"}
        assert store.cycles("demo").groupby("cell_id").size().tolist() == [3, 4, 5]
        rep2 = ingest(spec, workers=1)
        assert (rep2["status"] == "unchanged, skipped").sum() == 3       # only the broken file is retried


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
