import math
from pathlib import Path

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt

# =========================================================
# HOW TO USE
# =========================================================
# In every module and notebook, replace all the repeated
# mpl.rcParams / fig_sizes / legend_style blocks with:
#
#   from battery_analysis.style import (
#       apply_style, fig_sizes, fig_sizes_xps, xps_figsize,
#       legend_style, format_axes, ensure_figure_transparency,
#       SCATTER, CE_SCATTER, EIS_SCATTER,
#       LINE, PROFILE_LINE, CYCLE_PROFILE_LINE,
#       EIS_LINE, PARAM_LINE, RIC_LINE, DQDV_LINE,
#       BAR, SAVE_DPI, ALPHA_MIN, ALPHA_MAX,
#   )
#   apply_style()
#
# Then to tweak anything — marker size, font, linewidth —
# edit here once and it applies everywhere.
# =========================================================


# =========================================================
# FIGURE SIZES  (width × height, inches)
# =========================================================
fig_sizes = {
    "small":  (2.2, 2.1),
    "medium": (3.2, 2.1),
    "large":  (4.2, 2.1),
    "huge":   (6.42, 2.1),
}

# =========================================================
# XPS FIGURE SIZES
# =========================================================

XPS_REFERENCE_WIDTH = 2.0   # inches — panel width at which lines/fonts look "normal"

# XPS figures are saved at their FINAL printed size so that
# when imported into Affinity Designer no rescaling is needed
# and the 10pt font stays exactly 10pt.
#
# Page width = 6.42" (matches fig_sizes["huge"]).
# Gap between panels in Affinity = 0.08" per gap.
# Panel height is 2.44" per row.
#
# Usage:
#   figsize = xps_figsize(4)           # 4 panels in a row, single height
#   figsize = xps_figsize(2, rows=2)   # 2 panels in a row, double height
#
# rows=1 → single height (2.44")  — fitted peak or single spectrum
# rows=2 → double height (4.88")  — stacked survey

PAGE_WIDTH   = 6.42   # inches — full figure width (matches fig_sizes["huge"])
PANEL_GAP    = 0.08   # inches — gap between panels (mirror this in Affinity)

# XPS fitted peak panels are SQUARE — peaks are tall and narrow,
# a wide/flat aspect ratio squashes them. The width is determined by
# n_panels; height matches width automatically.
#
# The stacked SURVEY plot is different — it is tall (2× square height)
# because multiple spectra are stacked vertically.
SURVEY_HEIGHT_MULTIPLIER = 2.0   # survey height = width × this


def xps_figsize(n_panels, survey=False):
    """
    Return the correct (width, height) for an XPS panel so that
    n_panels placed side-by-side in Affinity fill the full page width
    with no rescaling — fonts stay at 10pt.

    Fitted peak panels are SQUARE (height = width).
    The survey plot uses survey=True for a taller stacked layout.

    Parameters
    ----------
    n_panels : int
        How many panels will sit in a row in Affinity (1–5).
    survey : bool
        False (default) = square fitted peak panel
        True            = tall stacked survey panel (height = width × 2)

    Examples
    --------
    figsize = xps_figsize(4)              # 4 fitted peaks → (1.545, 1.545)
    figsize = xps_figsize(2)              # 2 fitted peaks → (3.170, 3.170)
    figsize = xps_figsize(3)              # 3 fitted peaks → (2.087, 2.087)
    figsize = xps_figsize(2, survey=True) # survey, 2 wide  → (3.170, 6.340)
    figsize = xps_figsize(1, survey=True) # survey, full    → (6.420, 12.84)

    Reference table (fitted peaks, square)
    ---------------------------------------
    n=1 → (6.420, 6.420)
    n=2 → (3.170, 3.170)
    n=3 → (2.087, 2.087)
    n=4 → (1.545, 1.545)
    n=5 → (1.220, 1.220)
    """
    width  = round((PAGE_WIDTH - PANEL_GAP * (n_panels - 1)) / n_panels, 3)
    height = round(width * SURVEY_HEIGHT_MULTIPLIER, 3) if survey else width
    return (width, height)


# Backward-compatible aliases so existing xps.py / xps.ipynb code keeps working.
fig_sizes_xps = {
    "single": xps_figsize(2),              # two square fitted panels → (3.170, 3.170)
    "stack":  xps_figsize(2, survey=True), # survey, two wide         → (3.170, 6.340)
}


# =========================================================
# GLOBAL RC PARAMS
# =========================================================
RC_PARAMS = {
    "font.family":              "Arial",
    "font.size":                9.5,
    "text.color":               "black",
    "axes.labelcolor":          "black",
    "axes.edgecolor":           "black",
    "xtick.color":              "black",
    "ytick.color":              "black",
    "xtick.labelcolor":         "black",
    "ytick.labelcolor":         "black",
    "axes.linewidth":           0.65,
    "xtick.direction":          "out",
    "ytick.direction":          "out",
    "xtick.major.width":        0.65,
    "ytick.major.width":        0.65,
    "xtick.major.size":         4,
    "ytick.major.size":         4,
    "xtick.bottom":             True,
    "ytick.left":               True,
    "axes.labelpad":            2,
    "figure.facecolor":         "none",
    "axes.facecolor":           "none",
    "savefig.transparent":      True,
    "legend.frameon":           False,
    "svg.fonttype":             "none",
    "pdf.fonttype":             42,
    "path.simplify":            False,
    # "path.simplify_threshold":  0.1,
    "axes.grid":                False,
}


def apply_style():
    """Call once per notebook/module to apply global style."""
    mpl.rcParams.update(RC_PARAMS)
    # Silence "Font family 'Arial' not found" spam on machines without
    # Arial installed — matplotlib already falls back to a substitute
    # font automatically; the warning is just noise in every plot cell.
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


# =========================================================
# LEGEND
# =========================================================
legend_style = dict(
    fontsize=7,
    handlelength=1.0,
    handletextpad=0.25,
    labelspacing=0.25,
    frameon=False,
)


# =========================================================
# SCATTER MARKERS
# =========================================================
SCATTER = dict(
    marker_size=25,
    solid_edge_color="#AFABAB",
    solid_edge_width=0.11,
    hollow_edge_width=0.9,
)

CE_SCATTER = dict(
    marker_size=25,
    hollow_edge_width=0.9,
)

EIS_SCATTER = dict(
    marker_size=25,
    alpha=0.8,
)


# =========================================================
# LINE WIDTHS
# =========================================================
LINE              = dict(linewidth=0.8)   # connecting lines in scatter / CE
PROFILE_LINE      = dict(linewidth=1)   # voltage profiles
CYCLE_PROFILE_LINE= dict(linewidth=1)   # per-cycle profiles with alpha fade
EIS_LINE          = dict(linewidth=1)   # EIS Nyquist
PARAM_LINE        = dict(linewidth=1)   # EIS param vs cycle
RIC_LINE          = dict(linewidth=1)   # RIC plots
DQDV_LINE         = dict(linewidth=1)   # dQ/dV curves


# =========================================================
# BAR CHARTS  (SEM)
# =========================================================
BAR = dict(
    edgecolor="black",
    linewidth=1.2,
)


# =========================================================
# SAVE SETTINGS
# =========================================================
SAVE_DPI = 600


# =========================================================
# ALPHA FADE  (voltage profiles with multiple cycles)
# =========================================================
ALPHA_MIN = 0.3
ALPHA_MAX = 1.0


# =========================================================
# TICK CONTROL
# =========================================================
# The problem this solves: matplotlib's default tick locator (AutoLocator /
# MaxNLocator) picks "nice" round numbers independently of your axis
# limits, then only draws whichever of those land inside [lim0, lim1]. If
# your limits don't happen to divide evenly by whatever step the locator
# picked, you lose the tick at (or near) one or both ends — that's the
# "some values are missing" symptom. It can also silently draw a
# different number of ticks on visually-matched plots.
#
# compute_ticks() replaces that guesswork with an explicit, reproducible
# rule: ticks ALWAYS start exactly at lim[0] and end exactly at lim[1] —
# no exceptions, no dropped endpoints. You control spacing one of three
# ways (most specific wins):
#   1. `ticks=[...]`   — exact values, used verbatim.
#   2. `step=...`       — fixed spacing; if it doesn't divide the range
#                          evenly, every step is even EXCEPT the last one
#                          (shortened so lim[1] still gets its own tick),
#                          e.g. lim=(0, 240), step=50 -> 0,50,100,150,200,240.
#   3. `n=...`           — an exact tick COUNT, evenly spaced end to end
#                          (like np.linspace) — the default when neither
#                          of the above is given.
#
# Wire this up per-plot from YAML: every *PlotDefaults / *PlotEntry /
# *SheetPlot config below carries optional `xtick_step` / `ytick_step`
# (exact spacing) and `xticks_n` / `yticks_n` (exact count) fields —
# set one of those in the technique's YAML and format_axes() below picks
# it up automatically. Leave both unset and you still get a clean,
# complete set of ticks (DEFAULT_TICK_N of them) instead of matplotlib's
# default guess.

DEFAULT_TICK_N = 5   # matches what this project already used in most places


def compute_ticks(lim, step=None, n=None, ticks=None, decimals=8):
    """
    Return an explicit list of tick positions for one axis, guaranteed to
    start at lim[0] and end at lim[1] — see module docstring above for
    the three ways to control spacing. Returns None if lim is None.

    lim may be DESCENDING (lim[0] > lim[1]) — XPS binding-energy axes are
    plotted high-to-left/low-to-right by convention — and this still
    produces ticks running the same direction, endpoints included.
    """
    if ticks is not None:
        return list(ticks)
    if lim is None:
        return None
    lo, hi = lim
    if lo == hi:
        return [lo]
    direction = 1 if hi >= lo else -1

    if step is not None:
        step = abs(step) * direction
        n_steps = int(math.floor(round((hi - lo) / step, decimals)))
        vals = [lo + i * step for i in range(n_steps + 1)]
        if not math.isclose(vals[-1], hi, rel_tol=1e-9, abs_tol=10 ** -decimals):
            vals.append(hi)
    else:
        vals = list(np.linspace(lo, hi, n or DEFAULT_TICK_N))

    return [round(v, decimals) for v in vals]


def nice_limits(lo: float, hi: float, intervals: int = 4):
    """Round (lo, hi) outward to a 'nice' step (1, 2, 2.5 or 5 x 10^k) giving about
    `intervals` ticks: (-1.47, 1.58) -> (-2, 2, step 1); (-0.09, 0.21) -> (-0.1, 0.25, step 0.05)."""
    span = max(hi - lo, 1e-12)
    raw = span / intervals
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw - 1e-12)
    lo_n, hi_n = math.floor(round(lo / step, 9)) * step, math.ceil(round(hi / step, 9)) * step
    return (round(lo_n, 10), round(hi_n, 10), round(step, 10))


def format_axes(ax, xlim=None, ylim=None, xticks=None, yticks=None, *,
                 xtick_step=None, ytick_step=None, xticks_n=None, yticks_n=None, compact=False):
    """
    Consistent axis formatting — black ticks/labels, no grid.

    If xticks/yticks aren't given explicitly, they're computed from
    xlim/ylim via compute_ticks() (honoring xtick_step/ytick_step/
    xticks_n/yticks_n if given) instead of being left to matplotlib's
    default locator — so every plot that sets xlim/ylim gets complete,
    evenly-spaced ticks with no missing endpoint, by default.
    """
    if xlim is not None: ax.set_xlim(xlim)
    if ylim is not None: ax.set_ylim(ylim)
    if xticks is None and xlim is not None:
        xticks = compute_ticks(xlim, step=xtick_step, n=xticks_n)
    if yticks is None and ylim is not None:
        yticks = compute_ticks(ylim, step=ytick_step, n=yticks_n)
    if xticks is not None: ax.set_xticks(xticks)
    if yticks is not None: ax.set_yticks(yticks)
    if compact:                                   # 0, 0.5, 1 — not 0.0, 0.5, 1.0
        from matplotlib.ticker import FuncFormatter
        fmt = FuncFormatter(lambda v, _: f"{round(v, 10):g}".replace("-", "\u2212"))
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
    ax.tick_params(axis="both", which="both",
                   colors="black", labelcolor="black",
                   width=0.7, direction="out", length=4)
    ax.xaxis.label.set_color("black")
    ax.yaxis.label.set_color("black")
    for spine in ax.spines.values():
        spine.set_linewidth(0.7)
        spine.set_edgecolor("black")
    ax.grid(False)
    ax.set_facecolor("none")


def ensure_figure_transparency(fig):
    """Transparent figure background."""
    fig.patch.set_alpha(0.0)


# =========================================================
# LABEL SHORTENING  (sheet/cycle labels on axes, DRT row ticks)
# =========================================================
# Sheet labels are free-text (whatever a technique's YAML puts in its
# sheet_names:) so their length is unpredictable — "0h" next to
# "Precycle" next to "3.15V" next to "500 cycles". Mixed short/long
# labels on the same figure (DRT contour row ticks, EIS param-evolution
# x-ticks) make the figure visually non-uniform and, for DRT contours
# specifically, eat into the plot area unevenly. shorten_label() caps
# DISPLAY text at a fixed budget — deterministic and content-agnostic,
# so it does something sensible no matter what a label says, not just
# for the label names this project happens to use today. It only
# affects what's drawn on the figure; the full label is still used
# everywhere else (exports, database, legends' underlying data).

def shorten_label(text: str, max_len: int = 6) -> str:
    """Truncate *text* to at most max_len characters (ellipsis if cut)."""
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)].rstrip() + "…"


# =========================================================
# INLINE DISPLAY  (Jupyter / PyCharm notebook window)
# =========================================================
# Every plotting function in this project does fig.savefig(...) and then
# used to call plt.close(fig) immediately — which destroys the figure
# BEFORE the notebook cell finishes, so nothing ever showed up inline,
# no matter what backend was active. finish_figure() replaces that
# plt.close(fig) call: under Jupyter/PyCharm's inline backend it displays
# the figure in the cell's output (and the inline backend closes it for
# you afterward, so memory still doesn't accumulate across many plots in
# one run); anywhere else (a plain script, a headless export) it just
# closes it exactly like before — nothing changes there.

INLINE_BACKGROUND = None   # off by default -- figures stay genuinely transparent
# on screen, exactly like the original xps.py (fig.patch.set_alpha(0.0), plt.show(),
# plt.close(), nothing painted behind it). Pass inline_background="white" (or any
# color) to finish_figure() on a specific call if you ever want an opaque preview
# instead -- this constant just controls the default.


def finish_figure(fig, inline_background=INLINE_BACKGROUND):
    """
    Call once per figure, right after fig.savefig(...). Mirrors the
    original plt.show(); plt.close() -- figures are genuinely
    transparent on screen, same as the saved file. Under a headless
    "Agg" backend (a plain script run with no window) this just closes
    the figure; anywhere else (Jupyter, PyCharm's SciView, a plain
    interactive window) it calls plt.show() so the figure displays.
    """
    backend = mpl.get_backend().lower()
    if backend == "agg":
        plt.close(fig)
        return
    if inline_background is not None:
        fig.patch.set_facecolor(inline_background)
        fig.patch.set_alpha(1.0)
        for ax in fig.axes:
            ax.patch.set_facecolor(inline_background)
            ax.patch.set_alpha(1.0)
    plt.show()


def figures_dir(config, subfolder: str, batch=None) -> Path:
    """
    Resolve (and create) config.paths.figures_root/<subfolder> — one folder
    per technique. Figures are saved through batch_path(), which puts each
    batch in its own subfolder: Figures/<technique>/<batch>/<name>_<batch>.svg.
    `batch` is accepted only so existing calls keep working.

    Every technique's plot.py had its own copy of this exact 3-line
    function, identical apart from the subfolder name ("EIS", "DRT",
    "Cycle", ...). One shared version here means a change to how figure
    paths resolve only has to happen once.
    """
    d = config.resolve(config.paths.figures_root) / subfolder
    d.mkdir(parents=True, exist_ok=True)
    return d


def batch_path(folder, name: str, batch=None) -> Path:
    """Where a figure is saved: <technique folder>/<batch>/<name>_<batch>.svg.
    Each batch gets its own subfolder (Figures/GCD/Batch1/, Figures/GCD/Batch2/, ...)
    so batches never mix; the file name still carries the batch too, so a figure
    dragged into a slide deck is still identifiable. No batch -> the technique folder."""
    folder = Path(folder)
    if batch:
        folder = folder / str(batch)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / batch_filename(name, batch)


def batch_filename(name: str, batch=None) -> str:
    """'GCD.svg' + 'Batch1' -> 'GCD_Batch1.svg', so batches sit side by side in one
    technique folder. Left alone when there is no batch or the name already carries it."""
    if not batch or str(batch) in name:
        return name
    stem, dot, ext = name.rpartition(".")
    return f"{stem}_{batch}.{ext}" if dot else f"{name}_{batch}"


def chem_label(text) -> str:
    """Legend text with chemical subscripts: 'P2O5Al_Ref' -> P₂O₅Al_Ref (mathtext, regular font)."""
    return str(text).replace("P2O5", r"P$\mathregular{_2}$O$\mathregular{_5}$")


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th'."""
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# =========================================================
# PLOT DATA FOR COLLABORATORS (Origin / SigmaPlot)
# =========================================================
# Every figure saved inside a "Figures" folder also gets its plotted
# numbers + styling written to <exports_root>/PlotData/<same sub-folders>/<name>.xlsx/.csv
# (see plotting/plotdata.py). Set plotdata.ENABLED = False to switch it off.
from battery_analysis.plotting.plotdata import install_savefig_hook as _install_plot_data_hook  # noqa: E402

_install_plot_data_hook()
