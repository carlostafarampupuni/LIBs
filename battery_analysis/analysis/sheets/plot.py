"""
Curated-workbook figures (sheets.yaml).

Use this when the numbers already exist in a hand-built workbook — a collaborator's
processed results, or old data whose raw files are gone — and you only need the
figures in the house style (plus the Origin/SigmaPlot plot data that every figure
writes to Exports/PlotData/).

A sheet holds column pairs: an x column followed by one or more y columns, e.g.
    Time | Bare | Time1 | MXCu            (each y uses the nearest x on its left)
The y column header is the legend label; it is matched to config.yaml's samples
(ignoring a trailing replicate number: Bare1, Bare2 -> Bare) for the colour.

kind (per figure; sets axis labels and marker style, all overridable):
    gcd      voltage vs time (lines)         profile  voltage vs capacity (lines)
    cycle    capacity vs cycle (points)      ce       CE vs cycle (points)
    cycle_ce capacity + CE on a second axis (CE columns end in _CE)
    rate     capacity vs cycle (points, optional C-rate dividers)
    eis      Nyquist (lines + points)        tafel    log|i| vs η (lines)
    ftir     spectrum vs wavenumber (lines, x reversed)
    line / scatter   generic
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import apply_style, fig_sizes, legend_style, batch_filename, SAVE_DPI

PALETTE = ["#6B7280", "#E8746A", "#5B9BD5", "#70AD47", "#FFC000", "#7030A0", "#264478", "#9E480E"]

KINDS = {
    "gcd":      dict(xlabel="Time (h)", ylabel="Potential (V vs. Li/Li$^{+}$)", style="line"),
    "profile":  dict(xlabel="Capacity (mAh g$^{-1}$)", ylabel="Voltage (V)", style="line"),
    "cycle":    dict(xlabel="Cycle No.", ylabel="Capacity (mAh g$^{-1}$)", style="scatter"),
    "ce":       dict(xlabel="Cycle No.", ylabel="CE (%)", style="scatter"),
    "cycle_ce": dict(xlabel="Cycle No.", ylabel="Capacity (mAh g$^{-1}$)", y2label="CE (%)", style="scatter"),
    "rate":     dict(xlabel="Cycle No.", ylabel="Capacity (mAh g$^{-1}$)", style="scatter"),
    "eis":      dict(xlabel="Z$'$ (Ω)", ylabel="−Z$''$ (Ω)", style="linepoint"),
    "tafel":    dict(xlabel="η (V)", ylabel="log |i| (A cm$^{-2}$)", style="line"),
    "ftir":     dict(xlabel="Wavenumber (cm$^{-1}$)", ylabel="Transmittance (a.u.)", style="line", invert_x=True),
    "line":     dict(xlabel="", ylabel="", style="line"),
    "scatter":  dict(xlabel="", ylabel="", style="scatter"),
}


def _size(fs, default="medium"):
    return tuple(fs) if isinstance(fs, (list, tuple)) else fig_sizes.get(fs or default, fig_sizes[default])


def _base_name(label: str) -> str:
    return re.sub(r"[\s_\-]*\d+$", "", str(label).strip())


def read_sheet(workbook: Path, sheet: str, header_row: int = 0) -> pd.DataFrame:
    df = pd.read_excel(workbook, sheet_name=sheet, header=header_row, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    return df.loc[:, [c for c in df.columns if not c.startswith("Unnamed")]]


def sheet_series(df: pd.DataFrame, x_columns: Optional[List[str]] = None, pairs=None) -> List[tuple]:
    """[(x column, y column)] — explicit `pairs`, or each y with the nearest x column to its left.
    x columns: `x_columns`, else the first column's name and its numbered copies (Time, Time1, ...)."""
    if pairs:
        return [tuple(p) for p in pairs]
    cols = list(df.columns)
    if x_columns is None:
        stem = re.sub(r"\d+$", "", cols[0])
        x_columns = [c for c in cols if re.fullmatch(re.escape(stem) + r"\d*", c)]
    out, x = [], None
    for c in cols:
        if c in x_columns:
            x = c
        elif x is not None:
            out.append((x, c))
    return out


def _colour(label, colours: Dict[str, str], order: List[str]):
    for key in (label, _base_name(label)):
        if key in colours:
            return colours[key]
    base = _base_name(label)
    if base not in order:
        order.append(base)
    return PALETTE[order.index(base) % len(PALETTE)]


def _apply_axes(ax, f):
    for k, fn in (("xlim", ax.set_xlim), ("ylim", ax.set_ylim), ("xticks", ax.set_xticks), ("yticks", ax.set_yticks)):
        v = f.get(k)
        if v is None:
            continue
        if k.endswith("ticks") and isinstance(v, dict):          # {start, stop, step}
            v = np.arange(v["start"], v["stop"] + 1e-9 * abs(v["step"]), v["step"])
        fn(v)


def plot_figure(df: pd.DataFrame, f: dict, colours: Dict[str, str]):
    kind = f.get("kind", "line")
    spec = {**KINDS.get(kind, KINDS["line"]), **f}
    series = sheet_series(df, f.get("x_columns"), f.get("pairs"))
    keep = f.get("samples")                                      # list of y columns (or base names) to show
    if keep:
        series = [(x, y) for x, y in series if y in keep or _base_name(y) in keep]
    labels = f.get("labels") or {}

    apply_style()
    fig, ax = plt.subplots(figsize=_size(f.get("size"), "medium"))
    ax2 = ax.twinx() if kind == "cycle_ce" else None
    order, seen = [], set()
    for z, (xc, yc) in enumerate(series, start=1):
        x = pd.to_numeric(df[xc], errors="coerce")
        y = pd.to_numeric(df[yc], errors="coerce")
        ok = x.notna() & y.notna()
        if f.get("invert_y") is True or (isinstance(f.get("invert_y"), list) and yc in f["invert_y"]):
            y = -y
        elif kind == "eis" and f.get("invert_y") is None and y[ok].median() < 0:   # sheet holds Z'' -> plot -Z''
            y = -y
        is_ce = kind == "cycle_ce" and yc.upper().endswith("_CE")
        name = labels.get(yc, _base_name(yc[:-3] if is_ce else yc))
        c = _colour(name, colours, order)
        target = ax2 if is_ce else ax
        lab = None if (name in seen or is_ce) else name
        seen.add(name)
        style = spec["style"]
        if style == "line":
            target.plot(x[ok], y[ok], color=c, lw=f.get("lw", 1.0), zorder=z, label=lab)
        elif style == "linepoint":
            target.plot(x[ok], y[ok], color=c, lw=0.8, marker="o", ms=2.5, zorder=z, label=lab)
        else:
            target.scatter(x[ok], y[ok], s=f.get("marker_size", 14), color=c, zorder=z, label=lab,
                           marker="o" if not is_ce else "^", facecolors=c if not is_ce else "none",
                           edgecolors=c, linewidths=0.6)
    for xv in f.get("c_rate_lines") or []:
        ax.axvline(xv + 0.5, color="0.75", lw=0.5, ls="--", zorder=0)
    ax.set_xlabel(spec.get("xlabel", ""))
    ax.set_ylabel(spec.get("ylabel", ""))
    _apply_axes(ax, f)
    if ax2 is not None:
        ax2.set_ylabel(spec.get("y2label", "CE (%)"))
        _apply_axes(ax2, {"ylim": f.get("y2lim"), "yticks": f.get("y2ticks")})
    if spec.get("invert_x") and f.get("xlim") is None:
        ax.invert_xaxis()
    if f.get("hide_yaxis"):
        ax.set_ylabel(""); ax.set_yticklabels([])
    if f.get("legend", True) and seen:
        ax.legend(loc=f.get("legend_loc", "best"), **legend_style)
    return fig


def plot_batch(batch: dict, workbook: Path, colours: Dict[str, str], out_dir: Path, name: str,
               formats=("svg",), show: bool = False) -> List[Path]:
    """Every figure of one sheets.yaml batch; zooms are extra figures with their own axis settings."""
    written = []
    for fig_name, f in (batch.get("figures") or {}).items():
        df = read_sheet(workbook, f.get("sheet", fig_name), f.get("header_row", 0))
        variants = [(fig_name, f)] + [(z.get("name", f"{fig_name}_zoom{i + 1}"),
                                       {**f, "size": "small", "legend": False, **z})
                                      for i, z in enumerate(f.get("zooms") or [])]
        for vname, vf in variants:
            fig = plot_figure(df, vf, colours)
            path = Path(out_dir) / name / batch_filename(vname, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            for fmt in formats:
                p = path.with_suffix(f".{fmt}")
                fig.savefig(p, dpi=SAVE_DPI, transparent=True, bbox_inches="tight", pad_inches=0.02)
                written.append(p)
            plt.show() if show else plt.close(fig)
    return written
