"""
Build a DOE run sheet from doe.yaml.

Every factor gets a one-letter code (A, B, C, ...) used in the statistics.
Two-level factors are coded −1 / +1: for a categorical factor, the first level
listed is −1 and the second is +1; for a numeric factor, low is −1 and high +1
(a centre point, coded 0, is the midpoint).

Kinds of design (doe.yaml → kind):
    full              every combination of levels (2^k runs per replicate)
    fractional        a fraction of the full design, from `generators`
                      (e.g. "a b c abc" = 4 factors in 8 runs). Fewer cells,
                      but some effects are mixed up (aliased) with others.
    plackett_burman   screening: many factors, main effects only
    ccd               central composite (face-centred): finds curvature and
                      an optimum. Numeric factors only.

The run order is randomized (seeded, so it is repeatable): build and test the
cells in that order so drift in the glovebox, electrolyte or cycler is spread
over all conditions instead of hiding inside one of them.
"""
from __future__ import annotations

import string
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def load_doe(path) -> dict:
    """Read doe.yaml and check it. Returns the dict with factor codes added."""
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["_path"] = path
    factors = cfg.get("factors") or []
    if len(factors) < 2:
        raise ValueError("doe.yaml: list at least 2 factors under factors:")
    for i, f in enumerate(factors):
        f["code"] = string.ascii_uppercase[i]
        f.setdefault("type", "categorical")
        f.setdefault("label", f["name"])
        if len(f.get("levels", [])) != 2:
            raise ValueError(f"doe.yaml: factor {f['name']!r} needs exactly 2 levels (low, high)")
        if f["type"] == "numeric":
            f["levels"] = [float(v) for v in f["levels"]]
    cfg.setdefault("kind", "full")
    cfg.setdefault("replicates", 1)
    cfg.setdefault("center_points", 0)
    cfg.setdefault("seed", 1)
    cfg.setdefault("responses", [])
    if cfg["kind"] == "ccd" and any(f["type"] != "numeric" for f in factors):
        raise ValueError("doe.yaml: kind: ccd needs every factor to be numeric")
    if cfg["center_points"] and any(f["type"] != "numeric" for f in factors):
        raise ValueError("doe.yaml: centre points need every factor to be numeric "
                         "(a categorical factor like Al/Cu has no middle)")
    return cfg


def _coded_matrix(cfg) -> np.ndarray:
    import pyDOE3

    k = len(cfg["factors"])
    kind = cfg["kind"]
    if kind == "full":
        return pyDOE3.ff2n(k)
    if kind == "fractional":
        gen = cfg.get("generators")
        if not gen or len(gen.split()) != k:
            raise ValueError(f"doe.yaml: kind: fractional needs `generators` with {k} words, e.g. 'a b c abc'")
        return pyDOE3.fracfact(gen)
    if kind == "plackett_burman":
        return pyDOE3.pbdesign(k)
    if kind == "ccd":
        return pyDOE3.ccdesign(k, center=(0, 0), face="ccf")   # centre points added below
    raise ValueError(f"doe.yaml: unknown kind {kind!r}")


def _actual(f, coded: float):
    if f["type"] == "numeric":
        lo, hi = f["levels"]
        return round((lo + hi) / 2 + coded * (hi - lo) / 2, 10)
    return f["levels"][0] if coded < 0 else f["levels"][1]


def build_design(cfg) -> pd.DataFrame:
    """
    One row per cell to build, in randomized run order. Columns:
    run, std_order, replicate, <factor names> (actual settings),
    A, B, ... (coded −1/0/+1), cell (fill in the cycler file name), notes,
    and one empty column per response.
    """
    factors = cfg["factors"]
    base = _coded_matrix(cfg)
    n_center = int(cfg["center_points"])
    if n_center:
        base = np.vstack([base, np.zeros((n_center, len(factors)))])

    rows = []
    for rep in range(1, int(cfg["replicates"]) + 1):
        for std, coded in enumerate(base, start=1):
            row = {"std_order": std, "replicate": rep}
            for f, c in zip(factors, coded):
                row[f["name"]] = _actual(f, c)
            for f, c in zip(factors, coded):
                row[f["code"]] = float(c)
            rows.append(row)
    df = pd.DataFrame(rows)

    rng = np.random.default_rng(int(cfg["seed"]))
    df = df.iloc[rng.permutation(len(df))].reset_index(drop=True)
    df.insert(0, "run", np.arange(1, len(df) + 1))
    name = cfg.get("design_name", "DOE")
    df.insert(1, "cell_id", [f"{name}-{r:02d}" for r in df["run"]])
    df["cell"] = ""          # the cycler file name (stem), e.g. BareAl_Ref-3 — lets fill_from_gcd() find it
    df["notes"] = ""
    for r in cfg["responses"]:
        df[r["name"]] = np.nan
    return df


def factor_table(cfg) -> pd.DataFrame:
    return pd.DataFrame([{
        "code": f["code"], "factor": f["name"], "label": f["label"], "type": f["type"],
        "low (−1)": f["levels"][0], "high (+1)": f["levels"][1],
    } for f in cfg["factors"]])


def write_run_sheet(cfg, design: pd.DataFrame, out_dir, overwrite: bool = False) -> Path:
    """
    Save Data/DOE/<design_name>/run_sheet.xlsx (sheet 'runs' + sheet 'factors').
    An existing run sheet is never overwritten unless overwrite=True, because
    by then it may hold your measured results.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "run_sheet.xlsx"
    if path.exists() and not overwrite:
        print(f"Kept the existing run sheet (it may already hold results): {path}\n"
              f"Pass overwrite=True to replace it with a new randomization.")
        return path
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        design.to_excel(xl, sheet_name="runs", index=False)
        factor_table(cfg).to_excel(xl, sheet_name="factors", index=False)
    print(f"Wrote {len(design)} runs -> {path}")
    return path
