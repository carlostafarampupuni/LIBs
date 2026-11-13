"""
Plot-ready data for EVERY figure the project saves — so a collaborator
using Origin, SigmaPlot, Excel or Igor can redraw any figure exactly.

How it works: importing battery_analysis.plotting.style installs a small
hook on matplotlib's Figure.savefig. Whenever a figure is saved inside the
project's Figures folder, its plotted numbers and styling are written to the
project's exports folder, in the same sub-folders as the figure — Figures/
holds only figures, and every number is stored once:

    Figures/EIS/Batch1/FRA_Batch1.svg
    <exports>/PlotData/EIS/Batch1/FRA_Batch1.xlsx    data + style + annotations
    <exports>/PlotData/EIS/Batch1/FRA_Batch1.csv     data sheet only

<exports> is paths.exports_root in config.yaml (Project() registers it). For a
figure saved outside any project, the nearest Figures folder's sibling
Exports/ (or Results/Exports/) is used; if there is none, nothing is written.

No technique's plotting code needs to know about this. A plot function
can write a richer, figure-specific sheet itself (XPS does) and set
`fig._plot_data_written = True` so the generic export is skipped.

Data sheet layout (Origin import: Long Name / Units / Comments rows;
SigmaPlot: skip rows 2–3): one X and one Y column per plotted series,
side by side (series can have different lengths):

    row 1  long name   "<series> X" | "<series> Y" | ...
    row 2  units       x-axis label | y-axis label
    row 3  comments    how the series is drawn: line/marker/fill colour (hex), widths, sizes
    rows 4+            values

What is captured: lines, scatter points, filled areas (polygon outline),
bars, error bars, images / colour maps (grid), text labels and the axis
set-up (labels, limits, scales, ticks, legend). Filled contour plots keep
only their level list — export the underlying grid from the technique if
you need it.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

ENABLED = True              # set False to stop writing plot data for figures
PLOTDATA_FOLDER = "PlotData"
_FIG_SUFFIXES = {".svg", ".png", ".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".eps"}


def _hex(c) -> str:
    from matplotlib.colors import to_hex
    try:
        return to_hex(c, keep_alpha=False)
    except (ValueError, TypeError):
        try:
            return to_hex(np.asarray(c).reshape(-1, 4)[0], keep_alpha=False)
        except Exception:  # noqa: BLE001
            return str(c)


def _plain(text) -> str:
    """Mathtext -> plain text: '$\\mathrm{Z}_{re}$ (Ω)' -> 'Zre (Ω)'."""
    t = str(text or "")
    t = re.sub(r"\\mathrm\{([^}]*)\}|\\mathit\{([^}]*)\}|\\mathregular\{([^}]*)\}",
               lambda m: next(g for g in m.groups() if g is not None), t)
    t = re.sub(r"\\([A-Za-z]+)", lambda m: {"Omega": "Ω", "mu": "µ", "alpha": "α", "beta": "β",
                                             "theta": "θ", "tau": "τ", "lambda": "λ", "Delta": "Δ",
                                             "degree": "°", "circ": "°", "times": "×",
                                             "cdot": "·", "minus": "−"}.get(m.group(1), m.group(1)), t)
    return t.replace("$", "").replace("{", "").replace("}", "").replace("_", "").replace("^", "").strip()


def _alpha(a) -> str:
    return "" if a is None else f", alpha {round(float(a), 2)}"


class _Series:
    def __init__(self):
        self.cols: List[tuple] = []          # (long name, units, comment, values)

    def add_xy(self, name, x, y, xu, yu, comment):
        self.cols.append((f"{name} X", xu, comment, np.asarray(x, float)))
        self.cols.append((f"{name} Y", yu, comment, np.asarray(y, float)))

    def frame(self) -> pd.DataFrame:
        if not self.cols:
            return pd.DataFrame()
        n = max(len(c[3]) for c in self.cols)
        body = np.full((n, len(self.cols)), np.nan)
        for j, c in enumerate(self.cols):
            v = np.ravel(c[3])
            body[: len(v), j] = v
        head = pd.DataFrame([[c[0] for c in self.cols], [c[1] for c in self.cols], [c[2] for c in self.cols]])
        return pd.concat([head, pd.DataFrame(body)], ignore_index=True)


def _legend_names(ax) -> dict:
    """artist -> legend text, for artists that appear in the legend."""
    leg = ax.get_legend()
    out = {}
    if leg is None:
        return out
    texts = [t.get_text() for t in leg.get_texts()]
    handles = getattr(leg, "legend_handles", None) or getattr(leg, "legendHandles", [])
    labels = {a.get_label(): a for a in ax.get_children() if hasattr(a, "get_label")}
    for t in texts:
        if t in labels:
            out[id(labels[t])] = t
    return out


def extract(fig):
    """Figure -> (data DataFrame, style DataFrame, annotations DataFrame, errorbars DataFrame)."""
    from matplotlib.collections import PathCollection, PolyCollection, LineCollection, QuadMesh
    from matplotlib.container import BarContainer, ErrorbarContainer
    from matplotlib.image import AxesImage
    from matplotlib.lines import Line2D

    series, style, notes, errs, grids = _Series(), [], [], [], []
    style.append(("figure size (in)", f"{fig.get_size_inches()[0]:.3g} × {fig.get_size_inches()[1]:.3g}"))
    axes = [a for a in fig.axes if a.get_visible()]
    for k, ax in enumerate(axes):
        tag = f"ax{k + 1}" if len(axes) > 1 else ""
        pre = (tag + " ") if tag else ""
        xu, yu = _plain(ax.get_xlabel()), _plain(ax.get_ylabel())
        names = _legend_names(ax)
        bar_patches = set()
        style += [(f"{pre}x label", xu), (f"{pre}y label", yu),
                  (f"{pre}x limits", f"{ax.get_xlim()[0]:.6g} → {ax.get_xlim()[1]:.6g}"
                                     + (" (reversed)" if ax.xaxis_inverted() else "")),
                  (f"{pre}y limits", f"{ax.get_ylim()[0]:.6g} → {ax.get_ylim()[1]:.6g}"
                                     + (" (reversed)" if ax.yaxis_inverted() else "")),
                  (f"{pre}x scale / ticks", f"{ax.get_xscale()} / {[float(round(v, 6)) for v in ax.get_xticks()]}"),
                  (f"{pre}y scale / ticks", f"{ax.get_yscale()} / {[float(round(v, 6)) for v in ax.get_yticks()]}"),
                  (f"{pre}y axis shown", str(ax.spines['left'].get_visible() or ax.spines['right'].get_visible())),
                  (f"{pre}title", _plain(ax.get_title()))]
        for axis, lab in (("x", ax.get_xticklabels()), ("y", ax.get_yticklabels())):
            texts = [t.get_text() for t in lab]
            if any(t and not t.replace(".", "").replace("−", "").replace("-", "").isdigit() for t in texts):
                style.append((f"{pre}{axis} tick labels", " | ".join(_plain(t) for t in texts)))
        leg = ax.get_legend()
        if leg is not None:
            style.append((f"{pre}legend", " | ".join(_plain(t.get_text()) for t in leg.get_texts())))

        # bars (their rectangles are skipped from the generic patch handling)
        for c in ax.containers:
            if isinstance(c, BarContainer):
                rects = list(c.patches)
                bar_patches.update(id(r) for r in rects)
                if not rects:
                    continue
                horiz = getattr(c, "orientation", "vertical") == "horizontal"
                pos = [r.get_y() + r.get_height() / 2 if horiz else r.get_x() + r.get_width() / 2 for r in rects]
                val = [r.get_width() if horiz else r.get_height() for r in rects]
                nm = _plain(c.get_label()) if not str(c.get_label()).startswith("_") else f"{pre}bars"
                r0 = rects[0]
                series.add_xy(nm, pos, val, yu if horiz else xu, xu if horiz else yu,
                              f"bars{' horizontal' if horiz else ''}, fill {_hex(r0.get_facecolor())}, "
                              f"edge {_hex(r0.get_edgecolor())} {r0.get_linewidth()} pt, "
                              f"width {(r0.get_height() if horiz else r0.get_width()):.4g}")
            elif isinstance(c, ErrorbarContainer):
                data_line, caps, bars = c.lines
                if data_line is not None and not str(c.get_label()).startswith("_"):
                    names[id(data_line)] = c.get_label()
                for lc in bars or []:
                    for seg in lc.get_segments():
                        (x0, y0), (x1, y1) = seg[0], seg[-1]
                        errs.append({"axes": tag or "ax", "series": _plain(c.get_label()),
                                     "x": (x0 + x1) / 2, "y_low": min(y0, y1), "y_high": max(y0, y1),
                                     "x_low": min(x0, x1), "x_high": max(x0, x1), "color": _hex(lc.get_color())})

        for i, ln in enumerate(ax.get_lines()):
            if not isinstance(ln, Line2D) or not ln.get_visible():
                continue
            x, y = ln.get_xdata(orig=False), ln.get_ydata(orig=False)
            try:
                x, y = np.asarray(x, float), np.asarray(y, float)
            except (TypeError, ValueError):
                continue
            if x.size < 2 and y.size < 2:
                continue
            lab = names.get(id(ln)) or ln.get_label()
            nm = _plain(lab) if lab and not str(lab).startswith("_") else f"{pre}line {i + 1}"
            mk = ln.get_marker()
            desc = []
            if ln.get_linestyle() not in ("None", "", " ", "none"):
                desc.append(f"line {_hex(ln.get_color())} {ln.get_linewidth()} pt {ln.get_linestyle()}")
            if mk not in (None, "None", "", " ", "none"):
                desc.append(f"marker '{mk}' size {ln.get_markersize()}, face {_hex(ln.get_markerfacecolor())}, "
                            f"edge {_hex(ln.get_markeredgecolor())}")
            series.add_xy(nm, x, y, xu, yu, "; ".join(desc) + _alpha(ln.get_alpha()))

        for i, col in enumerate(ax.collections):
            if not col.get_visible():
                continue
            lab = col.get_label()
            nm = _plain(lab) if lab and not str(lab).startswith("_") else None
            if isinstance(col, QuadMesh):
                arr = np.asarray(col.get_array())
                coords = col.get_coordinates()
                grids.append((nm or f"{pre}map {i + 1}", coords, arr))
                continue
            if isinstance(col, PathCollection):
                off = col.get_offsets()
                if len(off) == 0:
                    continue
                sizes = col.get_sizes()
                fc = col.get_facecolors()
                ec = col.get_edgecolors()
                series.add_xy(nm or f"{pre}points {i + 1}", off[:, 0], off[:, 1], xu, yu,
                              f"scatter size {sizes[0] if len(sizes) else ''}, "
                              f"face {_hex(fc[0]) if len(fc) else 'none'}, edge {_hex(ec[0]) if len(ec) else 'none'}, "
                              f"edge width {col.get_linewidths()[0] if len(col.get_linewidths()) else ''}"
                              + _alpha(col.get_alpha()))
            elif isinstance(col, PolyCollection):
                for j, path in enumerate(col.get_paths()):
                    v = path.vertices
                    if len(v) < 3:
                        continue
                    fc = col.get_facecolors()
                    series.add_xy((nm or f"{pre}fill {i + 1}") + (f".{j + 1}" if len(col.get_paths()) > 1 else ""),
                                  v[:, 0], v[:, 1], xu, yu,
                                  f"filled area (closed outline), fill {_hex(fc[0]) if len(fc) else 'none'}"
                                  + _alpha(col.get_alpha() if col.get_alpha() is not None else
                                           (fc[0][3] if len(fc) else None)))
            elif isinstance(col, LineCollection) and not any(col in (c.lines[2] or ()) for c in ax.containers
                                                             if isinstance(c, ErrorbarContainer)):
                for j, seg in enumerate(col.get_segments()):
                    if len(seg) >= 2:
                        series.add_xy(f"{nm or pre + 'segments ' + str(i + 1)}.{j + 1}", seg[:, 0], seg[:, 1],
                                      xu, yu, f"line {_hex(col.get_colors()[0]) if len(col.get_colors()) else ''}")
            elif type(col).__name__ in ("QuadContourSet", "ContourSet") or hasattr(col, "levels"):
                style.append((f"{pre}contour levels", str(list(getattr(col, "levels", [])))))

        for im in ax.get_images():
            if isinstance(im, AxesImage):
                arr = np.asarray(im.get_array())
                if arr.ndim == 2:
                    x0, x1, y0, y1 = im.get_extent()
                    xs = np.linspace(x0, x1, arr.shape[1])
                    ys = np.linspace(y0, y1, arr.shape[0])
                    grids.append((_plain(im.get_label()) if not str(im.get_label()).startswith("_")
                                  else f"{pre}image", (xs, ys), arr))
                    style.append((f"{pre}image colormap", str(im.get_cmap().name)))

        for t in ax.texts:
            if t.get_text().strip():
                x, y = t.get_position()
                notes.append({"axes": tag or "ax", "text": _plain(t.get_text()), "x": x, "y": y,
                              "coordinates": "axes fraction" if t.get_transform() == ax.transAxes else "data",
                              "font size": t.get_fontsize(), "color": _hex(t.get_color()),
                              "rotation": t.get_rotation(), "style": t.get_style()})

    for t in fig.texts:
        if t.get_text().strip():
            x, y = t.get_position()
            notes.append({"axes": "figure", "text": _plain(t.get_text()), "x": x, "y": y,
                          "coordinates": "figure fraction", "font size": t.get_fontsize(),
                          "color": _hex(t.get_color()), "rotation": t.get_rotation(), "style": t.get_style()})

    return series.frame(), pd.DataFrame(style, columns=["item", "value"]), pd.DataFrame(notes), \
        pd.DataFrame(errs), grids


README = [
    "Data for one figure, laid out for Origin / SigmaPlot / Excel.",
    "Sheet 'data': rows 1–3 = Long Name, Units (axis labels), Comments (how each series is drawn).",
    "One X and one Y column per series; series can have different lengths.",
    "Filled areas are given as their closed outline (plot as a filled polygon).",
    "Sheet 'style': figure size, axis labels, limits (and whether reversed), scales, ticks, legend.",
    "Sheet 'annotations' (if present): every text label with its position and font.",
    "Sheet 'errorbars' / 'map …' (if present): error-bar ends; colour-map grids (first column/row = axes).",
]


def _grid_frame(coords, arr) -> pd.DataFrame:
    """A colour map as a table: rows = y, columns = x, cells = value."""
    arr = np.asarray(arr, dtype=float)
    if isinstance(coords, tuple):                          # image: axis vectors
        xs, ys = coords
        return pd.DataFrame(arr.reshape(len(ys), len(xs)), index=np.round(ys, 6), columns=np.round(xs, 6))
    cx, cy = coords[0, :, 0], coords[:, 0, 1]
    if arr.size == len(cy) * len(cx):                      # values at the grid points (gouraud / nearest)
        return pd.DataFrame(arr.reshape(len(cy), len(cx)), index=np.round(cy, 6), columns=np.round(cx, 6))
    if arr.size == (len(cy) - 1) * (len(cx) - 1):          # values per cell (flat): use cell centres
        return pd.DataFrame(arr.reshape(len(cy) - 1, len(cx) - 1),
                            index=np.round((cy[:-1] + cy[1:]) / 2, 6), columns=np.round((cx[:-1] + cx[1:]) / 2, 6))
    return pd.DataFrame({"value": arr.ravel()})             # unknown layout: values only


def write(fig, path_no_ext) -> List[Path]:
    """Write <path>.xlsx (all sheets) and <path>.csv (data sheet). Returns written paths.
    Every sheet is prepared first, so an error never leaves a half-written file."""
    data, style, notes, errs, grids = extract(fig)
    sheets = [("README", pd.DataFrame({"How to use": README}), dict(index=False)),
              ("data", data if not data.empty else pd.DataFrame({"note": ["no plotted series found"]}),
               dict(index=False, header=data.empty)),
              ("style", style, dict(index=False))]
    if not notes.empty:
        sheets.append(("annotations", notes, dict(index=False)))
    if not errs.empty:
        sheets.append(("errorbars", errs, dict(index=False)))
    for k, (name, coords, arr) in enumerate(grids[:20]):
        sheets.append((f"map {k + 1}", _grid_frame(coords, arr), dict()))

    p = Path(path_no_ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = [p.with_suffix(".xlsx")]
    with pd.ExcelWriter(out[0]) as xw:
        for name, df, kw in sheets:
            df.to_excel(xw, sheet_name=name, **kw)
    if not data.empty:
        c = p.with_suffix(".csv")
        data.to_csv(c, index=False, header=False, quoting=csv.QUOTE_MINIMAL, encoding="utf-8-sig")
        out.append(c)
    return out


_ROOTS: List[tuple] = []          # (figures root, plot-data root), registered by Project()


def register_project(figures_root, exports_root) -> None:
    """Called by Project(): figures saved under figures_root get their data under
    exports_root/PlotData/, in the same sub-folders."""
    pair = (Path(figures_root).resolve(), Path(exports_root).resolve() / PLOTDATA_FOLDER)
    if pair not in _ROOTS:
        _ROOTS.insert(0, pair)


def data_path_for(figure_path) -> "Path | None":
    """Where a figure's plot data goes (without suffix), or None if it isn't a project figure."""
    fig = Path(figure_path).resolve()
    for figs_root, data_root in _ROOTS:
        try:
            rel = fig.relative_to(figs_root)
        except ValueError:
            continue
        return data_root / rel.parent / fig.stem
    # not registered: nearest "Figures" folder, and an exports folder beside it
    for anc in fig.parents:
        if anc.name.lower() == "figures":
            rel = fig.relative_to(anc)
            for base in (anc.parent, anc.parent / "Results"):
                for name in ("Exports", "exports"):
                    if (base / name).is_dir():
                        return base / name / PLOTDATA_FOLDER / rel.parent / fig.stem
            return None
    return None


def install_savefig_hook():
    """Wrap Figure.savefig once so every saved project figure gets its plot data."""
    from matplotlib.figure import Figure
    if getattr(Figure.savefig, "_plot_data_hook", False):
        return

    original = Figure.savefig

    def savefig(self, fname, *args, **kwargs):
        result = original(self, fname, *args, **kwargs)
        try:
            if ENABLED and isinstance(fname, (str, Path)) and not getattr(self, "_plot_data_written", False):
                p = Path(fname)
                target = data_path_for(p) if p.suffix.lower() in _FIG_SUFFIXES else None
                if target is not None:
                    write(self, target)
        except Exception as exc:  # noqa: BLE001 — never let data export break a figure save
            print(f"⚠️  plot data not written for {Path(str(fname)).name}: {type(exc).__name__}: {exc}")
        return result

    savefig._plot_data_hook = True
    Figure.savefig = savefig
