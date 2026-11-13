"""
One row per cell from its cycle table: the numbers you compare across
hundreds of cells, and the inputs for lifetime models later.

Cycles that are not full cycles (rests, check-ups, partial tests) are left
out of fade metrics: formation cycles at the start (formation_cycles) are skipped,
and a cycle counts when its discharge capacity is at least
`min_fraction` of the cell's highest discharge capacity. Fade metrics need at
least `min_full_cycles` full cycles, so short tests (rate, GITT, HPPC, DCIR) get none.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def formation_cycles(c: pd.DataFrame, factor: float = 2.0) -> int:
    """
    Number of leading formation/check-in cycles: the first cycles that take more than
    `factor` × the median cycle duration (slow C/10–C/20 formation, long initial rests).
    Retention and end of life are measured from the first cycle after them.
    """
    if "duration_h" not in c or len(c) < 5:
        return 0
    d = c.sort_values("cycle")["duration_h"].to_numpy(float)
    med = np.nanmedian(d)
    n = 0
    while n < min(len(d) // 4, 20) and d[n] > factor * med:
        n += 1
    return n


def cell_features(cycles: pd.DataFrame, eol: float = 0.8, min_fraction: float = 0.3,
                  ref_cycles: int = 3, min_full_cycles: int = 10) -> dict:
    c = cycles.sort_values("cycle")
    qmax = c["discharge_ah"].max() if len(c) else 0.0
    n_form = formation_cycles(c)
    full = c.iloc[n_form:]
    full = full[full["discharge_ah"] >= min_fraction * qmax] if qmax > 0 else c.iloc[0:0]
    out = {"n_cycles": int(len(c)), "n_formation": n_form, "n_full_cycles": int(len(full)), "q_max_ah": float(qmax)}
    if len(full) < max(min_full_cycles, 2):     # GITT, HPPC, rate tests: no fade metrics
        return out
    q0 = float(full["discharge_ah"].iloc[:ref_cycles].median())
    ret = full["discharge_ah"] / q0
    below = full.loc[ret < eol, "cycle"]
    x, y = full["cycle"].to_numpy(float), ret.to_numpy(float)
    slope = np.polyfit(x, y, 1)[0] if len(full) >= 3 and np.ptp(x) > 0 else np.nan
    out.update({
        "q0_ah": q0,
        "q_last_ah": float(full["discharge_ah"].iloc[-1]),
        "retention_last": float(ret.iloc[-1]),
        "eol_cycle": int(below.iloc[0]) if len(below) else np.nan,      # NaN = not reached yet
        "fade_pct_per_100": float(-slope * 100 * 100) if np.isfinite(slope) else np.nan,
        "ce_median": float(full["ce"].median()),
        "mean_discharge_v_first": float(full["mean_discharge_v"].iloc[0]),
        "mean_discharge_v_last": float(full["mean_discharge_v"].iloc[-1]),
    })
    return out


def feature_table(store, dataset: str | None = None, **kw) -> pd.DataFrame:
    cyc = store.cycles(dataset)
    if cyc.empty:
        return pd.DataFrame()
    rows = [{"cell_id": cid, **cell_features(g, **kw)} for cid, g in cyc.groupby("cell_id", sort=False)]
    feats = pd.DataFrame(rows)
    cat = store.catalog()
    if not len(cat):
        return feats
    cat = cat.drop(columns=[c for c in feats.columns if c in cat.columns and c != "cell_id"])
    return cat.merge(feats, on="cell_id", how="right")
