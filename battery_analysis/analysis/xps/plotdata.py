"""
Plot data for fitted XPS figures (Origin / SigmaPlot / Excel) — written to the
same place every other figure's data goes (plotting/plotdata.py):

    Figures/XPS/<batch>/<sample>/<sample>_<region>_<batch>.svg
    <exports>/PlotData/XPS/<batch>/<sample>/<sample>_<region>_<batch>.xlsx   (+ .csv)

Richer than the generic figure export (battery_analysis/plotting/plotdata.py)
because it knows what each column means: component columns ALREADY INCLUDE the
background (exactly what the figure draws) — fill each one down to the
Background column. Surveys use the generic export.

Header rows (Origin import "Long Name / Units / Comments"; SigmaPlot: skip 2–3):
    row 1  Binding energy | Raw | Background | Envelope | LiF | ...
    row 2  eV | counts/s | ...
    row 3  how the column is drawn (colour, line, fill, % of area)
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from matplotlib.colors import to_hex

from . import fitting as xf
from . import pubplot as pp


def _sheet(cols: List[tuple]) -> pd.DataFrame:
    """cols = [(long name, units, comment, values)] -> DataFrame with 3 header rows."""
    n = max(len(c[3]) for c in cols)
    header = [[c[0] for c in cols], [c[1] for c in cols], [c[2] for c in cols]]
    body = np.full((n, len(cols)), np.nan)
    for j, c in enumerate(cols):
        body[: len(c[3]), j] = c[3]
    df = pd.DataFrame(body)
    return pd.concat([pd.DataFrame(header), df.round(6)], ignore_index=True)


def fit_sheet(o: xf.FitOutcome, fit_cfg: dict) -> pd.DataFrame:
    fs = pp.figure_settings(fit_cfg)
    rc = pp._region_plot_cfg(fs, o)
    lo, hi = sorted(rc["x_range"])
    m = (o.x >= lo) & (o.x <= hi)
    x, y, bg = o.x[m][::-1], o.y[m][::-1], o.background[m][::-1]          # high → low BE
    cols = [("Binding energy", "eV", f"x axis {rc['x_range'][0]}→{rc['x_range'][1]} (reversed)", x),
            ("Raw", "counts/s", f"hollow circles, black edge, size {fs['raw_marker_size']}", y),
            ("Background", "counts/s", f"line {to_hex(fs['background_color'])} {fs['background_width']} pt", bg)]
    if o.fitted:
        env = (o.envelope + o.background)[m][::-1]
        cols.append(("Envelope", "counts/s", f"line {to_hex(fs['envelope_color'])} {fs['envelope_width']} pt", env))
        colors = pp.component_colors(o.model)
        pct = dict(zip(o.table["component"], o.table["area %"]))
        for name, c in o.components.items():
            if pct.get(name, 0) < fs["min_area_pct_shown"]:
                continue
            cols.append((name, "counts/s",
                         f"fill {to_hex(colors[name])} alpha {fs['fill_alpha']} down to Background; "
                         f"outline {to_hex(fs['outline_color'])} {fs['outline_width']} pt; {pct[name]:.1f} % of area",
                         (c + o.background)[m][::-1]))
    return _sheet(cols)


def style_sheet(fit_cfg: dict) -> pd.DataFrame:
    from battery_analysis.plotting.style import fig_sizes, RC_PARAMS, legend_style
    fs = pp.figure_settings(fit_cfg)
    size = fs["size"]
    w, h = (tuple(size) if isinstance(size, (list, tuple)) else fig_sizes[size])
    rows = [("figure size (in)", f"{w} × {h}"), ("font", f"{RC_PARAMS['font.family']} {RC_PARAMS['font.size']} pt"),
            ("legend font", f"{fs.get('legend_fontsize') or legend_style['fontsize']} pt"),
            ("axis line width", f"{RC_PARAMS['axes.linewidth']} pt"), ("y axis", "hidden (no ticks, no line)"),
            ("x label", "B.E (eV)"), ("region label", "italic, top corner (e.g. 'F 1s')"),
            ("background", "transparent")]
    for r, rc in (fs.get("regions") or {}).items():
        rows.append((f"{r} x range / tick step", f"{rc.get('x_range')} / {rc.get('xtick_step', 'auto')} eV"))
    seen = {}
    for model in fit_cfg["regions"].values():
        for name, col in pp.component_colors(model).items():
            seen.setdefault(name, col)
    rows += [(f"colour: {n}", c) for n, c in seen.items()]
    return pd.DataFrame(rows, columns=["item", "value"])


README = [
    "Data for one fitted XPS figure (Origin / SigmaPlot / Excel).",
    "Sheet 'data': rows 1–3 = Long Name, Units, Comments (how each column is drawn).",
    "Component columns already include the background — fill each one down to 'Background'.",
    "Draw order: fills (largest first), thin black outlines, Background line, Raw circles, Envelope (red) on top.",
    "Binding energy runs high → low: reverse the x axis. Sheet 'style': sizes, fonts, ranges, all colours.",
]


def write_fit_data(o: xf.FitOutcome, fit_cfg: dict, path_no_ext) -> List[Path]:
    """<path>.xlsx (README, data, style) and <path>.csv (data) for one fitted-region figure."""
    p = Path(path_no_ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = fit_sheet(o, fit_cfg)
    with pd.ExcelWriter(p.with_suffix(".xlsx")) as xw:
        pd.DataFrame({"How to use": README}).to_excel(xw, sheet_name="README", index=False)
        data.to_excel(xw, sheet_name="data", index=False, header=False)
        style_sheet(fit_cfg).to_excel(xw, sheet_name="style", index=False)
    data.to_csv(p.with_suffix(".csv"), index=False, header=False, quoting=csv.QUOTE_MINIMAL, encoding="utf-8-sig")
    return [p.with_suffix(".xlsx"), p.with_suffix(".csv")]
