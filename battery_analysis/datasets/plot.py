"""
Publication figures for the dataset store, in the house style (plotting/style.py):
Arial 9.5 pt, fig_sizes small/medium/large/huge, round axis limits with evenly spaced
ticks (plotting/axes.py), transparent SVG at final printed size, saved as
Figures/Datasets/<dataset>/<name>_<dataset>.svg, with the plot data written to
Exports/PlotData/... for Origin/SigmaPlot like every other figure.

Figures are listed in datasets.yaml under `figures:` (see that file); plot_figures()
draws them all. kinds:

    capacity      discharge capacity vs cycle
    retention     capacity retention (%) vs cycle, with the end-of-life line
    ce            Coulombic efficiency (%) vs cycle
    profiles      voltage vs capacity of selected cycles of ONE cell (later = lighter)
    nyquist       EIS spectra of ONE cell over aging (later = lighter)
    eol_by_group  cycles to end of life, one column of points per group (box = quartiles)

Up to 8 cells: house scatter markers, one colour per cell, legend per cell.
More cells: thin lines coloured by `group` (datasets.yaml), one legend entry per group,
rasterized so the SVG stays small.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from battery_analysis.plotting.axes import from_yaml, set_axis
from battery_analysis.plotting.style import (
    ALPHA_MAX, ALPHA_MIN, SAVE_DPI, SCATTER, apply_style, batch_path, chem_label, ensure_figure_transparency,
    fig_sizes, finish_figure, legend_style,
)
from .cycles import cycle_index
from .features import formation_cycles
from .labels import assign_colours, cell_table

MANY = 8              # above this many cells: lines + group colours instead of markers per cell
TARGET_MARKERS = 60   # ~markers per cell in a scatter (every n-th cycle), so long tests stay readable


def _size(s, default="medium"):
    return tuple(s) if isinstance(s, (list, tuple)) else fig_sizes.get(s or default, fig_sizes[default])


def _save(fig, out_root: Path, name: str, batch: str) -> Path:
    path = batch_path(out_root, f"{name}.svg", batch)
    ensure_figure_transparency(fig)
    fig.savefig(path, dpi=SAVE_DPI, transparent=True, bbox_inches="tight", pad_inches=0.02)
    finish_figure(fig)
    return path


def _cap_unit(q_ah_max: float):
    return (1000.0, "mAh") if q_ah_max < 1.0 else (1.0, "Ah")


def _select(cells: pd.DataFrame, f: dict) -> pd.DataFrame:
    want = f.get("cells")
    if want:
        want = [want] if isinstance(want, str) else want
        cells = cells[cells["name"].apply(lambda n: any(fnmatch.fnmatch(n, p) for p in want))]
    excl = f.get("exclude") or []
    if excl:
        cells = cells[~cells["name"].apply(lambda n: any(fnmatch.fnmatch(n, p) for p in excl))]
    return cells


def _axes(ax, f, xdata, ydata, xlabel, ylabel, pad=0.03):
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    xs = np.concatenate([np.asarray(x, float) for x in xdata]) if xdata else np.array([0.0, 1.0])
    ys = np.concatenate([np.asarray(y, float) for y in ydata]) if ydata else np.array([0.0, 1.0])
    xs, ys = xs[np.isfinite(xs)], ys[np.isfinite(ys)]
    set_axis(ax, "x", xs.min() if xs.size else 0, xs.max() if xs.size else 1, **from_yaml(f, "x"))
    set_axis(ax, "y", ys.min() if ys.size else 0, ys.max() if ys.size else 1, pad=pad, **from_yaml(f, "y"))


def _legend(ax, f, handles=None, title=None):
    if f.get("legend", True) is False:
        return
    loc = f.get("legend_loc", "best")
    kw = dict(legend_style, loc=loc)
    if loc == "outside":                                 # right of the axes, top-aligned
        kw.update(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0)
    if title:
        kw.update(title=title, title_fontsize=legend_style.get("fontsize", 7))
    ax.legend(handles=handles, **kw) if handles else ax.legend(**kw)


# --------------------------------------------------------------- per-cycle kinds
_Y = {
    "capacity": lambda c, k: (c["discharge_ah"] * k, "Discharge capacity ({u})"),
    "retention": lambda c, k: (c["retention"] * 100, "Capacity retention (%)"),
    "ce": lambda c, k: (c["ce"] * 100, "Coulombic efficiency (%)"),
}


def _per_cycle(store, cells, f, kind):
    cyc = store.cycles(f.get("dataset"))
    cyc = cyc[cyc["cell_id"].isin(cells["cell_id"])]
    min_frac = f.get("min_fraction", 0.3)
    parts = []
    for cid, c in cyc.groupby("cell_id", sort=False):
        c = c.sort_values("cycle")
        if f.get("skip_formation", True):                # retention from the first cycle after formation
            c = c.iloc[formation_cycles(c):]
        qmax = c["discharge_ah"].max()
        c = c[c["discharge_ah"] >= min_frac * qmax] if qmax > 0 else c.iloc[0:0]
        if len(c):
            c = c.assign(retention=c["discharge_ah"] / c["discharge_ah"].iloc[: f.get("ref_cycles", 3)].median())
            parts.append(c)
    data = pd.concat(parts) if parts else pd.DataFrame()
    k, unit = _cap_unit(data["discharge_ah"].max() if len(data) else 1.0)

    apply_style()
    fig, ax = plt.subplots(figsize=_size(f.get("size"), "medium"))
    many = len(cells) > MANY
    xs, ys, handles, ylabel = [], [], {}, ""
    if many and f.get("aggregate") and len(data):
        # one median line per group with its interquartile band: readable with hundreds of cells
        y_all, ylabel = _Y[kind](data, k)
        data = data.assign(_y=y_all.to_numpy(), _g=data["cell_id"].map(cells.set_index("cell_id")["group"]))
        colour = cells.drop_duplicates("group").set_index("group")["colour"]
        for g in sorted(data["_g"].dropna().unique()):
            q = data[data["_g"] == g].groupby("cycle")["_y"].quantile([0.25, 0.5, 0.75]).unstack()
            n = data[data["_g"] == g].groupby("cycle")["cell_id"].nunique()
            # only while most of the batch is still on test: the median of the few survivors jumps
            n_group = int((cells["group"] == g).sum())
            q = q[(n >= f.get("min_cells", 3)) & (n >= f.get("min_cells_fraction", 0.5) * n_group)]
            ax.fill_between(q.index, q[0.25], q[0.75], color=colour[g], alpha=0.2, lw=0, rasterized=True)
            ax.plot(q.index, q[0.5], color=colour[g], lw=1.0, label=chem_label(g))
            xs.append(q.index.to_numpy()); ys.append(q[[0.25, 0.75]].to_numpy().ravel())
        ylabel = ylabel.format(u=unit)
        if kind == "retention" and f.get("eol_line", 80):
            ax.axhline(f.get("eol_line", 80), color="black", lw=0.6, ls="--", zorder=0)
        _axes(ax, f, xs, ys, f.get("xlabel", "Cycle number"), f.get("ylabel", ylabel))
        _legend(ax, f, title=cells["group_name"].iloc[0] or None)
        return fig
    for z, (_, cell) in enumerate(cells.iterrows(), start=1):
        c = data[data["cell_id"] == cell["cell_id"]] if len(data) else data
        if c.empty:
            continue
        y, ylabel = _Y[kind](c, k)
        x = c["cycle"]
        xs.append(x); ys.append(y)
        if many:
            ax.plot(x, y, color=cell["colour"], lw=f.get("lw", 0.5), alpha=f.get("alpha", 0.55),
                    rasterized=True, zorder=1)
            key = cell["group"] or cell["label"]
            handles.setdefault(key, plt.Line2D([], [], color=cell["colour"], lw=1.2, label=chem_label(key)))
            continue
        else:
            every = f.get("every") or max(1, int(np.ceil(len(c) / TARGET_MARKERS)))
            text = cell["label"] if f.get("legend_text", "label") == "label" else \
                f"{cell['label']} ({cell['group']})" if cell["group"] else cell["label"]
            ax.scatter(x.iloc[::every], y.iloc[::every], s=f.get("marker_size", SCATTER["marker_size"] * 0.4),
                       color=cell["colour"], edgecolors=SCATTER["solid_edge_color"],
                       linewidth=SCATTER["solid_edge_width"], zorder=z, label=chem_label(text))
    ylabel = ylabel.format(u=unit)
    if kind == "retention" and f.get("eol_line", 80):
        ax.axhline(f.get("eol_line", 80), color="black", lw=0.6, ls="--", zorder=0)
    _axes(ax, f, xs, ys, f.get("xlabel", "Cycle number"), f.get("ylabel", ylabel))
    if many:
        title = cells["group_name"].iloc[0] if cells["group"].ne("").any() else None
        groups = [handles[k] for k in sorted(handles)]
        if len(groups) <= 10:
            _legend(ax, f, handles=groups, title=title)
    else:
        _legend(ax, f)
    return fig


# ------------------------------------------------------------------ one-cell kinds
def _profiles(store, cells, f):
    cell = cells.iloc[0]
    ts = store.timeseries(cell["cell_id"])
    ts = ts.assign(cycle=cycle_index(ts))
    have = sorted(ts["cycle"].unique())
    cycles = f.get("cycles") or [have[i] for i in np.linspace(0, len(have) - 1, min(5, len(have))).astype(int)]
    q = store.cycles(cell["dataset"])
    k, unit = _cap_unit(q.loc[q["cell_id"] == cell["cell_id"], "discharge_ah"].max())
    apply_style()
    fig, ax = plt.subplots(figsize=_size(f.get("size"), "medium"))
    xs, ys = [], []
    alphas = np.linspace(ALPHA_MAX, ALPHA_MIN, len(cycles)) if len(cycles) > 1 else [ALPHA_MAX]
    for a, cy in zip(alphas, cycles):
        d = ts[ts["cycle"] == cy]
        t, i, v = d["test_time_second"].to_numpy(), d["current_ampere"].to_numpy(), d["voltage_volt"].to_numpy()
        dq = np.r_[0.0, np.diff(t)] * np.abs(i) / 3600.0 * k
        label = f.get("cycle_labels", {}).get(cy, f"Cycle {cy}")
        parts = {"charge": (i > 0,), "discharge": (i < 0,)}.get(f.get("parts", "both"), (i > 0, i < 0))
        for mask in parts:
            if mask.any():
                qq = np.cumsum(dq * mask)[mask]
                ax.plot(qq, v[mask], color=cell["colour"], alpha=a, lw=f.get("lw", 1.0), label=label)
                xs.append(qq); ys.append(v[mask]); label = None
    _axes(ax, f, xs, ys, f.get("xlabel", f"Capacity ({unit})"), f.get("ylabel", "Voltage (V)"), pad=0.02)
    _legend(ax, f)
    return fig


def _minmax_decimate(ts: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """Keep the min and max voltage point of each of max_points/2 bins: the trace looks the
    same (every pulse spike survives) with a fraction of the points. Nothing is averaged."""
    if not max_points or len(ts) <= max_points:
        return ts
    n_bins = max_points // 2
    b = np.arange(len(ts)) * n_bins // len(ts)
    v = ts["voltage_volt"].to_numpy()
    df = pd.DataFrame({"b": b, "v": v})
    keep = np.union1d(df.groupby("b")["v"].idxmin().to_numpy(), df.groupby("b")["v"].idxmax().to_numpy())
    i = ts["current_ampere"].to_numpy()                      # keep current extremes too (pulse heights)
    dfi = pd.DataFrame({"b": b, "i": i})
    keep = np.union1d(keep, np.union1d(dfi.groupby("b")["i"].idxmin().to_numpy(), dfi.groupby("b")["i"].idxmax().to_numpy()))
    return ts.iloc[keep]


def _voltage_time(store, cells, f):
    """Voltage (and optionally current) vs time of ONE cell: pulse, GITT, HPPC, DCIR tests."""
    cell = cells.iloc[0]
    ts = store.timeseries(cell["cell_id"], columns=["test_time_second", "voltage_volt", "current_ampere"])
    ts = _minmax_decimate(ts, f.get("max_points", 40000))
    t = (ts["test_time_second"] - ts["test_time_second"].iloc[0]) / 3600.0
    apply_style()
    fig, ax = plt.subplots(figsize=_size(f.get("size"), "large"))
    ax.plot(t, ts["voltage_volt"], color=cell["colour"], lw=f.get("lw", 0.8), rasterized=len(ts) > 20000,
            label=chem_label(cell["label"]))
    _axes(ax, f, [t], [ts["voltage_volt"]], f.get("xlabel", "Time (h)"), f.get("ylabel", "Voltage (V)"), pad=0.02)
    if f.get("current", True):
        ax2 = ax.twinx()
        ax2.plot(t, ts["current_ampere"], color="0.6", lw=0.5, rasterized=len(ts) > 20000, zorder=0)
        ax2.set_ylabel("Current (A)")
        y2 = {"lim": f["y2lim"]} if f.get("y2lim") else {}
        set_axis(ax2, "y", ts["current_ampere"].min(), ts["current_ampere"].max(), pad=0.05, **y2)
        ax.set_zorder(ax2.get_zorder() + 1); ax.patch.set_visible(False)
    _legend(ax, f)
    return fig


def _nyquist(store, cells, f):
    cell = cells.iloc[0]
    e = store.eis(cell["dataset"])
    e = e[e["cell_id"] == cell["cell_id"]]
    cycles = f.get("cycles") or sorted(e["cycle"].unique())
    scale, unit = (1000.0, "mΩ") if e["z_real_ohm"].abs().max() < 1 else (1.0, "Ω")
    apply_style()
    fig, ax = plt.subplots(figsize=_size(f.get("size"), "medium"))
    xs, ys = [], []
    alphas = np.linspace(ALPHA_MAX, ALPHA_MIN, len(cycles)) if len(cycles) > 1 else [ALPHA_MAX]
    for a, cy in zip(alphas, cycles):
        d = e[e["cycle"] == cy].sort_values("frequency_hertz", ascending=False)
        x, y = d["z_real_ohm"] * scale, -d["z_imag_ohm"] * scale
        ax.plot(x, y, "-o", color=cell["colour"], alpha=a, lw=0.8, ms=2.5, mec="none", label=f"{cy}")
        xs.append(x); ys.append(y)
    _axes(ax, f, xs, ys, f"Z′ ({unit})", f"−Z″ ({unit})")
    if f.get("equal_aspect", True):
        ax.set_aspect("equal", adjustable="box")
    _legend(ax, f, title=f.get("legend_title", "Cycle"))
    return fig


def _eol_by_group(store, cells, f):
    from .features import cell_features
    cyc = store.cycles(f.get("dataset"))
    rows = []
    for _, cell in cells.iterrows():
        c = cyc[cyc["cell_id"] == cell["cell_id"]]
        feat = cell_features(c, eol=f.get("eol", 0.8)) if len(c) else {}
        rows.append({"group": cell["group"] or cell["label"], "colour": cell["colour"],
                     "y": feat.get(f.get("metric", "eol_cycle"), np.nan)})
    d = pd.DataFrame(rows).dropna(subset=["y"])
    order = f.get("order") or sorted(d["group"].unique())
    apply_style()
    fig, ax = plt.subplots(figsize=_size(f.get("size"), "large"))
    rng = np.random.default_rng(0)
    ys = []
    for k, g in enumerate(order):
        y = d.loc[d["group"] == g, "y"].to_numpy()
        if not len(y):
            continue
        ax.boxplot([y], positions=[k], widths=0.55, showfliers=False, patch_artist=True,
                   boxprops=dict(facecolor="none", edgecolor="black", lw=0.65),
                   medianprops=dict(color="black", lw=0.9), whiskerprops=dict(lw=0.65), capprops=dict(lw=0.65))
        ax.scatter(k + rng.uniform(-0.16, 0.16, len(y)), y, s=SCATTER["marker_size"] * 0.5,
                   color=d.loc[d["group"] == g, "colour"].iloc[0], edgecolors=SCATTER["solid_edge_color"],
                   linewidth=SCATTER["solid_edge_width"], zorder=3)
        ys.append(y)
    rot = f.get("rotation", 30)
    ax.set_xticks(range(len(order)), [chem_label(g) for g in order], rotation=rot, ha="right" if rot else "center")
    ax.set_xlim(-0.6, len(order) - 0.4)
    ax.set_xlabel(f.get("xlabel", cells["group_name"].iloc[0] or ""))
    ax.set_ylabel(f.get("ylabel", "Cycles to 80% capacity"))
    all_y = np.concatenate(ys) if ys else np.array([0, 1])
    set_axis(ax, "y", 0, all_y.max(), **from_yaml(f, "y"))
    return fig


KINDS = {"capacity": lambda s, c, f: _per_cycle(s, c, f, "capacity"),
         "retention": lambda s, c, f: _per_cycle(s, c, f, "retention"),
         "ce": lambda s, c, f: _per_cycle(s, c, f, "ce"),
         "profiles": _profiles, "voltage_time": _voltage_time, "nyquist": _nyquist, "eol_by_group": _eol_by_group}


def plot_figures(store, spec, names=None) -> dict:
    """Draw every figure in datasets.yaml → figures: (or only `names`). Returns {name: path}."""
    from battery_analysis.plotting.plotdata import register_project
    proj = spec["_project_dir"]
    figures_root = proj / spec.get("figures_root", "Figures")
    out_root = figures_root / "Datasets"
    register_project(figures_root, proj / spec.get("exports_root", "Exports"))
    written = {}
    for name, f in (spec.get("figures") or {}).items():
        if names and name not in names:
            continue
        cells = _select(cell_table(store, spec, f.get("dataset")), f)
        if cells.empty:
            print(f"{name}: no cells match — skipped")
            continue
        if not cells["group"].ne("").any():
            cells = assign_colours(cells)          # colours from the house palette for THIS figure's cells
        if f.get("colour"):
            cells = cells.assign(colour=f["colour"])
        fig = KINDS[f["kind"]](store, cells, f)
        written[name] = _save(fig, out_root, name, f.get("dataset", "all"))
    return written
