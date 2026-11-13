"""
Publication figures for XPS — one file per sample × region, one survey per
sample and one stacked survey, sized and styled like every other figure in
the project (battery_analysis.plotting.style), ready to assemble in Affinity.

Works for every kind of input (see sources.py): your own fits and fits
imported from XPSPeak/CasaXPS-style exports produce identical-looking figures.

Style (matches the original XPS figures):
  * x-axis only — no y-axis, no top/left/right spines, transparent background
  * ticks from compute_ticks() (always land on both ends of x_range)
  * "B.E (eV)" label, region name in italics
  * raw = hollow markers, envelope = red line, components = coloured fills
    with thin outlines; background drawn but not in the legend
  * legend lists chemical species only, with proper sub/superscripts
    (Li2CO3 -> Li₂CO₃), placed automatically in empty space so the peaks
    fill the panel

Survey: full spectrum, charge-corrected, every scanned core level labelled
at its position — black if detected and fitted, grey if "no signal". This
is the visual argument that a region is deconvolved only when its peak
exists in the survey. Stacked survey: all (selected) samples in one figure.

Everything is configured in the `figures:` section of the fit YAML.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import apply_style, compute_ticks, fig_sizes, legend_style, SAVE_DPI
from battery_analysis.discovery import color_for

from . import fitting as xf

# Fallback component colours (Okabe–Ito based, colour-blind safe, no red: red is the envelope)
PALETTE = ["#E69F00", "#56B4E9", "#009E73", "#0072B2", "#CC79A7", "#F0E442",
           "#999999", "#A6761D", "#7570B3", "#66A61E"]

# Nominal core-level BEs (eV) used to place survey labels
CORE_LEVELS = {"C1s": 285.0, "O1s": 531.0, "F1s": 685.0, "N1s": 400.0, "P2p": 134.0,
               "Li1s": 55.0, "Ti2p": 459.0, "S2p": 168.0, "Si2p": 100.0, "Cl2p": 199.0,
               "Mn2p": 642.0, "Ni2p": 855.0, "Co2p": 780.0, "Cu2p": 933.0, "Al2p": 74.0,
               "Na1s": 1071.0, "B1s": 191.0}

DEFAULTS = {
    "size": "small",
    "formats": ["svg"],
    "min_area_pct_shown": 1.0,
    "legend": "inside",            # inside | separate | both | none
    "legend_loc": "upper left",    # preferred corner; the other is tried if this one covers data
    "legend_fontsize": None,       # None = project legend_style
    "raw_marker_size": 4,
    "raw_every": 1,
    "fill_alpha": 0.75,
    "outline_color": "black",
    "outline_width": 0.4,
    "envelope_color": "red",
    "envelope_width": 1.0,
    "background_color": "0.45",
    "background_width": 0.6,
    "region_label": True,
    "headroom": 0.06,              # minimum space above the tallest point (fraction of peak height)
    "max_headroom": 1.0,           # never add more than this to fit the legend
    "pretty_labels": True,         # Li2CO3 -> Li₂CO₃, " / " -> "/"
}

# Arial for sub/superscripts too, so formulas match the rest of the text
MATHTEXT = {"mathtext.fontset": "custom", "mathtext.rm": "Arial", "mathtext.it": "Arial:italic",
            "mathtext.bf": "Arial:bold", "mathtext.sf": "Arial", "mathtext.default": "regular"}

COMPACT_LEGEND = dict(labelspacing=0.12, handlelength=0.9, handleheight=0.65, handletextpad=0.3,
                      borderaxespad=0.15, borderpad=0.1, columnspacing=0.6)


def _apply():
    apply_style()
    mpl.rcParams.update(MATHTEXT)


# ──────────────────────────────────────────────────────────────────────
# Labels
# ──────────────────────────────────────────────────────────────────────
def pretty_region(region: str) -> str:
    """'C1s' -> 'C 1s', 'Ti2p' -> 'Ti 2p'."""
    return re.sub(r"^([A-Z][a-z]?)(\d.*)$", r"\1 \2", region)


ELEMENTS = set("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Sm Eu Gd
Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi M""".split())   # M = generic metal


def _parse_formula(tok: str):
    """'LixPOyFz' -> [('Li','x'),('P',''),('O','y'),('F','z')], or None if not a formula."""
    out, i = [], 0
    while i < len(tok):
        if tok[i:i + 2] in ELEMENTS and len(tok[i:i + 2]) == 2:
            el, i = tok[i:i + 2], i + 2
        elif tok[i] in ELEMENTS:
            el, i = tok[i], i + 1
        else:
            return None
        m = re.match(r"\d+|[xyz](?![a-z])", tok[i:])
        n = m.group(0) if m else ""
        i += len(n)
        out.append((el, n))
    return out or None


def _formula_tex(tok: str) -> str:
    charge = ""
    m = re.match(r"^(.*[A-Za-z])(\d*\+)$", tok)          # Ti2+, N+, Mn3+  -> charge
    if not m:
        m = re.match(r"^(.*[A-Za-z0-9])(-)$", tok)        # PF6-            -> charge
    if m:
        tok, charge = m.group(1), m.group(2)
    atoms = _parse_formula(tok)
    if atoms is None:
        return tok + charge
    if not any(n for _, n in atoms) and not charge:
        return tok                                  # plain symbols / acronyms (PVDF, LiF, C)
    body = ""
    for el, n in atoms:
        body += el
        if n:
            body += f"$_{{\\mathit{{{n}}}}}$" if n in "xyz" else f"$_{{{n}}}$"
    if charge:
        body += f"$^{{{charge.replace('-', chr(8722))}}}$"
    return body


def format_label(name: str) -> str:
    """'Li2CO3 / CO3' -> Li₂CO₃/CO₃; 'LixPOyFz' -> LiₓPOᵧF_z; 'Ti2+' -> Ti²⁺; 'SO4 2-' -> SO₄²⁻."""
    name = re.sub(r"\s*/\s*", "/", name)
    name = re.sub(r"(\S)\s+(\d+[+-])(?=$|[\s/,)])", r"\1\2", name)   # 'SO4 2-' -> 'SO42-' handled below
    parts = re.split(r"([\s/–=(),])", name)
    out = []
    for p in parts:
        mm = re.match(r"^(.*?[A-Za-z]\d*?)(\d[+-])$", p) if re.search(r"\d\d[+-]$", p) else None
        if mm and _parse_formula(mm.group(1)):     # SO42- = SO4 with 2- charge
            out.append(_formula_tex(mm.group(1)).rstrip() + f"$^{{{mm.group(2).replace('-', chr(8722))}}}$")
        else:
            out.append(_formula_tex(p) if p and p not in " /–=()," else p)
    return "".join(out)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────
def figure_settings(fit_cfg: dict) -> dict:
    return {**DEFAULTS, **((fit_cfg.get("raw", {}) or {}).get("figures", {}) or {})}


def component_colors(model: xf.RegionModel) -> Dict[str, str]:
    return {c.name: (c.color or PALETTE[i % len(PALETTE)]) for i, c in enumerate(model.components)}


def _figsize(size):
    return tuple(size) if isinstance(size, (list, tuple)) else fig_sizes[size]


def _legend_kw(fs):
    kw = {**legend_style, **COMPACT_LEGEND}
    if fs.get("legend_fontsize"):
        kw["fontsize"] = fs["legend_fontsize"]
    return kw


def _style_xaxis(ax, x_range, xtick_step=None, xticks_n=None):
    ax.set_xlim(*x_range)                    # x_range given high → low (XPS convention)
    ax.set_xticks(compute_ticks(x_range, step=xtick_step, n=xticks_n))
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{round(v, 6):g}"))
    ax.set_xlabel("B.E (eV)")
    ax.set_ylabel("")
    ax.set_yticks([])
    ax.tick_params(axis="y", left=False, labelleft=False)
    ax.tick_params(axis="x", colors="black", direction="out")
    for side in ("top", "left", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("black")
    ax.grid(False)
    ax.set_facecolor("none")
    ax.figure.patch.set_alpha(0.0)


def _region_plot_cfg(fs: dict, o: xf.FitOutcome) -> dict:
    r = dict((fs.get("regions", {}) or {}).get(o.region, {}) or {})
    if "x_range" not in r:                    # default: the data range, high → low
        r["x_range"] = [float(np.ceil(o.x.max())), float(np.floor(o.x.min()))]
    return r


def _save(fig, path_no_ext: Path, formats) -> List[Path]:
    path_no_ext.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for fmt in formats:
        p = path_no_ext.with_suffix(f".{fmt}")
        fig.savefig(p, format=fmt, dpi=SAVE_DPI, transparent=True, bbox_inches="tight", pad_inches=0.02)
        out.append(p)
    return out


def _bbox(ax, artist):
    return artist.get_window_extent(ax.figure.canvas.get_renderer())


def _covered(ax, artist, px, py) -> int:
    """How many data points fall inside an artist's box (display coordinates)."""
    bb = _bbox(ax, artist).expanded(1.04, 1.10)
    d = ax.transData.transform(np.column_stack([px, py]))
    return int(np.sum((d[:, 0] >= bb.x0) & (d[:, 0] <= bb.x1) & (d[:, 1] >= bb.y0) & (d[:, 1] <= bb.y1)))


# ──────────────────────────────────────────────────────────────────────
# Fitted region
# ──────────────────────────────────────────────────────────────────────
_LABEL_SPOTS = {"top right": (0.98, 0.98, "right", "top"), "top left": (0.02, 0.98, "left", "top"),
                "top center": (0.5, 0.98, "center", "top")}


def _layout(ax, handles, names, label, px, py, lo, peak_h, fs):
    """
    Smallest headroom at which the legend and the region label fit without
    covering data or each other. Tries both top corners for the legend and
    three spots for the label; raises the top only when nothing fits.
    """
    leg_corners = [fs["legend_loc"]] + [c for c in ("upper left", "upper right") if c != fs["legend_loc"]]
    ax.figure.canvas.draw()
    head, best = float(fs["headroom"]), None
    while True:
        ax.set_ylim(lo - 0.03 * peak_h, lo + peak_h * (1 + head))
        for corner in leg_corners if names else [None]:
            spots = ["top right", "top left", "top center"]
            if corner == "upper right":
                spots = ["top left", "top center", "top right"]
            for spot in spots if label else [None]:
                arts = []
                if names:
                    arts.append(ax.legend(handles, names, loc=corner, **_legend_kw(fs)))
                if label:
                    x0, y0, ha, va = _LABEL_SPOTS[spot]
                    arts.append(ax.text(x0, y0, label, transform=ax.transAxes, ha=ha, va=va, style="italic"))
                cost = sum(_covered(ax, a, px, py) for a in arts)
                if len(arts) == 2 and _bbox(ax, arts[0]).overlaps(_bbox(ax, arts[1])):
                    cost += 1000
                for a in arts:
                    a.remove()
                if best is None or cost < best[0]:
                    best = (cost, corner, spot, head)
                if cost == 0:
                    break
            if best[0] == 0:
                break
        if best[0] == 0 or head >= fs["max_headroom"]:
            break
        head += 0.05
    _, corner, spot, head = best
    ax.set_ylim(lo - 0.03 * peak_h, lo + peak_h * (1 + head))
    if names:
        ax.legend(handles, names, loc=corner, **_legend_kw(fs))
    if label:
        x0, y0, ha, va = _LABEL_SPOTS[spot]
        ax.text(x0, y0, label, transform=ax.transAxes, ha=ha, va=va, style="italic")


def plot_fit(o: xf.FitOutcome, fit_cfg: dict, legend: Optional[str] = None):
    """One publication panel for one fitted (or no-signal) spectrum. Returns (fig, legend_items)."""
    _apply()
    fs = figure_settings(fit_cfg)
    rc = _region_plot_cfg(fs, o)
    legend = legend or fs["legend"]

    fig, ax = plt.subplots(figsize=_figsize(rc.get("size", fs["size"])))
    x_range = rc["x_range"]
    m = (o.x >= min(x_range)) & (o.x <= max(x_range))
    x, y, bg = o.x[m], o.y[m], o.background[m]

    items = []
    if o.fitted:
        colors = component_colors(o.model)
        pct = dict(zip(o.table["component"], o.table["area %"]))
        shown = [(n, c[m]) for n, c in o.components.items() if pct.get(n, 0) >= fs["min_area_pct_shown"]]
        # fills: largest first, so small components are painted on top and stay visible
        for name, c in sorted(shown, key=lambda t: -float(xf._trapz(t[1], x)) if len(x) > 1 else 0):
            ax.fill_between(x, bg, bg + c, color=colors[name], alpha=fs["fill_alpha"], lw=0, zorder=2)
        # outlines after all fills, so no outline is hidden under a neighbour's fill
        for name, c in shown:
            ax.plot(x, bg + c, color=fs["outline_color"], lw=fs["outline_width"], zorder=3)
        items = [(name, colors[name]) for name, _ in shown]

    ax.plot(x, bg, color=fs["background_color"], lw=fs["background_width"], zorder=3)
    k = max(1, int(fs["raw_every"]))
    ax.scatter(x[::k], y[::k], s=fs["raw_marker_size"], facecolor="white", edgecolor="black",
               linewidths=0.4, zorder=4)
    env = (o.envelope + o.background)[m] if o.fitted else None
    if env is not None:
        ax.plot(x, env, color=fs["envelope_color"], lw=fs["envelope_width"], zorder=5)

    lo = float(np.min(bg)) if len(bg) else float(np.min(y))
    peak_h = float(np.max(y)) - lo
    _style_xaxis(ax, x_range, rc.get("xtick_step"), rc.get("xticks_n"))

    handles = [plt.Rectangle((0, 0), 1, 1, fc=c, ec=fs["outline_color"], lw=fs["outline_width"],
                             alpha=fs["fill_alpha"]) for _, c in items]
    names = ([format_label(n) if fs["pretty_labels"] else n for n, _ in items]
             if items and legend in ("inside", "both") else [])
    _layout(ax, handles, names, pretty_region(o.region) if fs["region_label"] else None,
            np.concatenate([x, x]), np.concatenate([y, env if env is not None else y]), lo, peak_h, fs)
    return fig, items


def save_legend(items, path_no_ext: Path, fit_cfg: dict, ncol: int = 1) -> List[Path]:
    """A legend on its own (transparent), for placing freely in Affinity."""
    _apply()
    fs = figure_settings(fit_cfg)
    fig = plt.figure(figsize=(2.2, 0.17 * max(1, len(items)) / ncol + 0.1))
    handles = [plt.Rectangle((0, 0), 1, 1, fc=c, ec=fs["outline_color"], lw=fs["outline_width"],
                             alpha=fs["fill_alpha"]) for _, c in items]
    labels = [format_label(n) if fs["pretty_labels"] else n for n, _ in items]
    fig.legend(handles, labels, loc="center left", ncol=ncol, **_legend_kw(fs))
    fig.patch.set_alpha(0.0)
    paths = _save(fig, path_no_ext, fs["formats"])
    plt.close(fig)
    return paths


# ──────────────────────────────────────────────────────────────────────
# Surveys
# ──────────────────────────────────────────────────────────────────────
def survey_settings(fs: dict) -> dict:
    return {"x_range": [1200, 0], "xtick_step": 300, "label_size": 6.5,
            "auger": [{"label": "F KLL", "be": 832}, {"label": "O KLL", "be": 978}],
            "extra_labels": [], **(fs.get("survey", {}) or {})}


def read_survey(source, fit_cfg: Optional[dict] = None) -> pd.DataFrame:
    """
    The workbook's survey sheet: BE + one column per sample. With fit_cfg, columns are
    renamed like the fitted spectra: settings.sample_names (workbook position -> name),
    then the spelling of the batch `samples:` list (case-insensitive: MxCu -> MXCu).
    """
    xl = pd.ExcelFile(source)
    sheet = next((s for s in xl.sheet_names if "survey" in s.lower()), None)
    if sheet is None:
        raise ValueError(f"No survey sheet in {source}")
    df = xf.read_avantage_sheet(xl, sheet)
    if fit_cfg:
        names = {str(k): str(v) for k, v in ((fit_cfg.get("settings") or {}).get("sample_names") or {}).items()}
        canon = {s.lower(): s for s in (fit_cfg.get("samples") or [])}
        df = df.rename(columns=lambda c: c if c == "BE" else canon.get(names.get(c, c).lower(), names.get(c, c)))
    return df


def survey_shifts(survey_source, fit_cfg: dict, names) -> Dict[str, float]:
    """Charge shift per (renamed) sample, from the workbook's C1s using the same renaming."""
    data = xf.read_avantage_workbook(survey_source)
    rn = {str(k): str(v) for k, v in ((fit_cfg.get("settings") or {}).get("sample_names") or {}).items()}
    canon = {s.lower(): s for s in (fit_cfg.get("samples") or [])}
    data = {r: d.rename(columns=lambda c: c if c == "BE" else canon.get(rn.get(c, c).lower(), rn.get(c, c)))
            for r, d in data.items()}
    return xf.compute_shifts(data, fit_cfg["settings"], [n for n in names if any(n in d.columns for d in data.values())])


def _survey_trace(be, counts, shift, xr):
    x = np.asarray(be, float) + shift
    y = np.asarray(counts, float)
    o = np.argsort(x)
    x, y = x[o], y[o]
    msk = (x >= min(xr)) & (x <= max(xr))
    x, y = x[msk], y[msk]
    return x, (y - y.min()) / (y.max() - y.min())


def _survey_labels(status: Dict[str, bool], sv: dict):
    labels = [(pretty_region(r), CORE_LEVELS[r], det) for r, det in status.items() if r in CORE_LEVELS]
    labels += [(a["label"], float(a["be"]), True) for a in (sv["auger"] or [])]
    labels += [(e["label"], float(e["be"]), bool(e.get("detected", True))) for e in (sv["extra_labels"] or [])]
    return labels


def _draw_survey_labels(ax, labels, top_at, sv, xr, data_top):
    """
    Rotated labels just above the local maximum. Each label's real rendered
    height is measured, and labels closer than 6 % of the range are pushed
    up until they no longer overlap.
    """
    min_gap = 0.06 * abs(xr[0] - xr[1])
    ax.set_ylim(-0.03, data_top * 1.45)            # provisional; the final range is ≥ this
    renderer = ax.figure.canvas.get_renderer()
    inv = ax.transData.inverted()

    def height(text):
        t = ax.text(0, 0, text, rotation=90, fontsize=sv["label_size"])
        bb = t.get_window_extent(renderer)
        t.remove()
        return float(inv.transform((0, bb.y1))[1] - inv.transform((0, bb.y0))[1])

    pad = 0.03 * data_top
    placed = []                                    # (position, y_start, y_end)
    for text, pos, detected in sorted(labels, key=lambda t: t[1]):
        top = top_at(pos)
        h = height(text)
        ylab = top + pad
        moved = True
        while moved:                               # push up until clear of every close neighbour
            moved = False
            for px, y0, y1 in placed:
                if abs(px - pos) < min_gap and ylab < y1 and y0 < ylab + h:
                    ylab, moved = y1 + 0.5 * pad, True
        placed.append((pos, ylab, ylab + h))
        ax.text(pos, ylab, text, rotation=90, ha="center", va="bottom", fontsize=sv["label_size"],
                color="black" if detected else "0.6")
        if not detected:
            ax.plot([pos, pos], [top + 0.005, ylab - 0.01], color="0.6", lw=0.4, ls=":")
    return max([p[2] for p in placed] + [0.0])     # top of the highest label


def plot_survey(sample: str, be: np.ndarray, counts: np.ndarray, shift: float,
                status: Dict[str, str], fit_cfg: dict):
    """Survey for one sample. `status` = {region: status}; detected ('fitted') black, absent grey."""
    _apply()
    fs = figure_settings(fit_cfg)
    sv = survey_settings(fs)
    xr = sv["x_range"]
    x, yn = _survey_trace(be, counts, shift, xr)
    fig, ax = plt.subplots(figsize=_figsize(sv.get("size", fs["size"])))
    ax.plot(x, yn, color="black", lw=0.7)

    def top_at(pos):
        w = (x >= pos - 6) & (x <= pos + 6)
        return float(yn[w].max()) if w.any() else 0.0

    labels = _survey_labels({r: s == "fitted" for r, s in status.items()}, sv)
    ymax = _draw_survey_labels(ax, labels, top_at, sv, xr, data_top=1.0)
    ax.set_ylim(-0.03, max(1.0, ymax) + 0.04)
    _style_xaxis(ax, xr, sv.get("xtick_step"), sv.get("xticks_n"))
    return fig


def _sample_colors(fit_cfg: dict, names: Sequence[str], overrides: dict,
                   all_names: Sequence[str] = ()) -> Dict[str, str]:
    """Stack colours: YAML overrides, then the project config.yaml sample colours, then the project palette."""
    proj = {}
    cfg_path = Path(fit_cfg.get("path", ".")).parent / "config.yaml"
    if cfg_path.exists():
        import yaml
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            proj = {str(s.get("name")): s.get("color") for s in (data.get("samples") or []) if s.get("color")}
        except Exception:  # noqa: BLE001
            proj = {}
    # palette index = position among ALL samples, so hiding one never recolours the others
    return {n: (overrides or {}).get(n) or proj.get(n) or color_for(all_names.index(n) if n in all_names else i)
            for i, n in enumerate(names)}


def plot_survey_stack(survey_df: pd.DataFrame, shifts: Dict[str, float], status: Dict[str, Dict[str, str]],
                      fit_cfg: dict):
    """
    All samples' surveys in one figure, first sample on top. Which samples,
    their order and colours: `figures.survey.stack` in the YAML. Element labels
    sit above the top trace; black = detected in at least one shown sample.
    """
    _apply()
    fs = figure_settings(fit_cfg)
    sv = survey_settings(fs)
    st = {"samples": None, "offset": 1.0, "colors": {}, "sample_labels": True, **(sv.get("stack") or {})}
    xr = st.get("x_range", sv["x_range"])
    names = [s for s in (st["samples"] or list(survey_df.columns[1:])) if s in survey_df.columns]
    missing = [s for s in (st["samples"] or []) if s not in survey_df.columns]
    if missing:
        print(f"⚠️  stacked survey: not in the survey sheet: {missing}")
    if not names:
        return None                       # none of the stacked samples is in this survey sheet
    colors = _sample_colors(fit_cfg, names, st["colors"], list(survey_df.columns[1:]))

    fig, ax = plt.subplots(figsize=_figsize(st.get("size", sv.get("size", fs["size"]))))
    n = len(names)
    traces = []
    for i, s in enumerate(names):
        x, yn = _survey_trace(survey_df["BE"].values, survey_df[s].values, shifts.get(s, 0.0), xr)
        y = yn + (n - 1 - i) * st["offset"]                  # first sample on top
        traces.append((x, y))
        ax.plot(x, y, color=colors[s], lw=0.7)
        if st["sample_labels"]:      # name on the high-BE (left) end, where the survey is flat
            left = x > max(xr) - 0.15 * abs(xr[0] - xr[1])
            base = float(np.max(y[left])) if left.any() else float(y[-1])
            ax.text(max(xr) - 0.01 * abs(xr[0] - xr[1]), base + 0.06, s, ha="left", va="bottom",
                    fontsize=sv["label_size"], color=colors[s])

    def top_at(pos):
        vals = [float(y[(x >= pos - 6) & (x <= pos + 6)].max()) for x, y in traces
                if ((x >= pos - 6) & (x <= pos + 6)).any()]
        return max(vals) if vals else 0.0

    regions = {r for s in names for r in status.get(s, {})}
    detected = {r: any(status.get(s, {}).get(r) == "fitted" for s in names) for r in regions}
    ymax = _draw_survey_labels(ax, _survey_labels(detected, sv), top_at, sv, xr,
                               data_top=max(float(y.max()) for _, y in traces))
    ax.set_ylim(-0.03, max(ymax, (n - 1) * st["offset"] + 1.0) + 0.04)
    _style_xaxis(ax, xr, sv.get("xtick_step"), sv.get("xticks_n"))
    return fig


# ──────────────────────────────────────────────────────────────────────
# Batch export
# ──────────────────────────────────────────────────────────────────────
def export_publication_figures(results: Sequence[xf.FitOutcome], survey_source, fit_cfg: dict, out_dir,
                               samples: Optional[Sequence[str]] = None,
                               regions: Optional[Sequence[str]] = None,
                               survey: bool = True, show: bool = False,
                               batch: Optional[str] = None) -> List[Path]:
    """
    Writes (batch = fit_cfg["batch"] unless given; same layout as every other technique):
        <out_dir>/<batch>/<sample>/<sample>_<region>_<batch>.<fmt>   one per sample × region
        (figure data for Origin/SigmaPlot -> <exports>/PlotData/XPS/..., see plotting/plotdata.py)
        <out_dir>/<batch>/<sample>/<sample>_Survey_<batch>.<fmt>     one survey per sample
        <out_dir>/<batch>/Survey_stack_<batch>.<fmt>                 selected samples, stacked
        <out_dir>/<batch>/legends/<region>_legend_<batch>.<fmt>      if figures.legend is separate/both
    survey_source: an Avantage workbook with a survey sheet (None = skip surveys).
    samples: defaults to the batch's `samples:` list (None = all).
    """
    from battery_analysis.plotting.style import batch_filename

    batch = batch or fit_cfg.get("batch")
    samples = samples if samples is not None else fit_cfg.get("samples")
    root = Path(out_dir) / batch if batch else Path(out_dir)
    name = lambda stem: batch_filename(stem, batch)          # noqa: E731
    fs = figure_settings(fit_cfg)
    written = []
    sel = [o for o in results if (samples is None or o.sample in samples)
           and (regions is None or o.region in regions)]

    legend_items: Dict[str, Dict[str, str]] = {}
    from .plotdata import write_fit_data
    from battery_analysis.plotting.plotdata import data_path_for
    for o in sel:
        fig, items = plot_fit(o, fit_cfg)
        fig._plot_data_written = True            # richer XPS-specific data written below instead
        stem = name(f"{o.sample}_{o.region}")
        written += _save(fig, root / o.sample / stem, fs["formats"])
        target = data_path_for((root / o.sample / stem).with_suffix(".svg"))
        if target is not None:
            written += write_fit_data(o, fit_cfg, target)
        plt.show() if show else plt.close(fig)
        legend_items.setdefault(o.region, {}).update(dict(items))

    if fs["legend"] in ("separate", "both"):
        for region, items in legend_items.items():
            if items:
                written += save_legend(list(items.items()), root / "legends" / name(f"{region}_legend"), fit_cfg)

    if survey and survey_source is not None:
        sv_df = read_survey(survey_source, fit_cfg)
        names = [s for s in sv_df.columns[1:] if samples is None or s in samples]
        shifts = survey_shifts(survey_source, fit_cfg, names)
        status = {s: {o.region: o.status for o in results if o.sample == s} for s in names}
        for s in names:
            fig = plot_survey(s, sv_df["BE"].values, sv_df[s].values, shifts.get(s, 0.0), status[s], fit_cfg)
            written += _save(fig, root / s / name(f"{s}_Survey"), fs["formats"])
            plt.show() if show else plt.close(fig)
        if names and (survey_settings(fs).get("stack") or {}).get("enabled", True):
            fig = plot_survey_stack(sv_df, shifts, status, fit_cfg)
            if fig is not None:
                written += _save(fig, root / name("Survey_stack"), fs["formats"])
                plt.show() if show else plt.close(fig)
    elif survey:
        print("ℹ️  no survey workbook among the sources — surveys skipped "
              "(set survey_source for this batch in the YAML to add one)")
    return written
