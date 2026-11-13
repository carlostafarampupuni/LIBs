"""
Tafel figures, in the project's figure style (plotting/style.py):

  per state   E (V) vs log10(i / A cm-2): charge and discharge points, the Excel-method
              lines (fixed alpha, through the OCV), the Butler–Volmer curve, the OCV
  summary     i0 and Rct vs state of charge, one marker per method (and per sample)

Figures go to Figures/Tafel/<batch>/...; their data for Origin/SigmaPlot is
written automatically to Exports/PlotData/Tafel/<batch>/...
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import numpy as np
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import apply_style, fig_sizes, legend_style, batch_filename, SAVE_DPI
from battery_analysis.analysis.pulse.geometry import FARADAY, R_GAS, tafel_slope_per_V

PALETTE = ["#5B9BD5", "#E8746A", "#70AD47", "#FFC000", "#7030A0", "#6B7280"]
# short legend names: marker per method, colour per sample
METHOD_STYLE = {"tafel": ("o", "Tafel"), "excel": ("v", "Excel"), "butler_volmer": ("s", "BV"),
                "tafel_region": ("^", "Region"), "linear": ("D", "Linear")}


def _size(fs):
    return tuple(fs) if isinstance(fs, (list, tuple)) else fig_sizes.get(fs, fig_sizes["small"])


def _save(fig, path, formats):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for fmt in formats:
        p = path.with_suffix(f".{fmt}")
        fig.savefig(p, dpi=SAVE_DPI, transparent=True, bbox_inches="tight", pad_inches=0.02)
        out.append(p)
    return out


def _sample_colour(sample, figs, k=0):
    return (figs.get("_colours") or {}).get(sample.sample) or PALETTE[k % len(PALETTE)]


def _slopes(fit, T):
    """(anodic, cathodic) slope in decades per volt for the drawn Tafel lines."""
    if np.isfinite(fit.get("slope_a_dec_per_V", np.nan)):
        return fit["slope_a_dec_per_V"], fit["slope_c_dec_per_V"]
    return (tafel_slope_per_V(fit["alpha_a"], T) if np.isfinite(fit.get("alpha_a", np.nan)) else np.nan,
            tafel_slope_per_V(fit["alpha_c"], T) if np.isfinite(fit.get("alpha_c", np.nan)) else np.nan)


def _state_ranges(state, figs):
    """Data extent of one figure: E of the points, log i of the points and of the i0 line."""
    pts = state.points
    if not len(pts):
        return np.array([]), np.array([])
    E = (state.ocv_V + pts["eta_V"]).to_numpy(float)
    y = pts["log_i"].to_numpy(float)
    fit = state.fits.get(figs.get("fit_lines", "tafel"), {})
    if fit.get("ok") and np.isfinite(fit.get("log_i0", np.nan)):
        y = np.append(y, fit["log_i0"])
    return np.append(E, state.ocv_V), y


def plot_state(sample, state, figs: dict, colour=None, xaxis=None, yaxis=None):
    """
    One clean Tafel figure (no legend, no text; labels go on in Affinity):
      points      discharge in the sample colour, charge grey (charge_color: same | "#hex")
      OCV         solid grey vertical line (show_ocv: false hides it)
      fit lines   the two Tafel lines of `fit_lines` (default tafel: slopes fitted to the
                  points, meeting at the OCV), dashed grey across the whole box, and a dashed
                  horizontal line at log i0 where they cross
    xaxis / yaxis: {"lim", "step", "ticks"} or {"data": (min, max)} from export_figures, so a
    batch (or a sample) shares one scale; see plotting/axes.py.
    """
    from battery_analysis.plotting.axes import set_axis
    apply_style()
    T = float(sample.settings.get("temperature_K", 298.15))
    colour = colour or _sample_colour(sample, figs)
    cc = figs.get("charge_color", "grey")
    c_charge = colour if cc == "same" else (cc if str(cc).startswith("#") else "0.6")
    line_c = figs.get("line_color", "0.35")
    ocv_c = figs.get("ocv_color", "0.6")
    fig, ax = plt.subplots(figsize=_size(figs.get("size", "small")))
    pts = state.points
    ref = state.ocv_V

    Ed, yd = _state_ranges(state, figs)
    xaxis = xaxis or {"data": (Ed.min(), Ed.max()) if Ed.size else (np.nan, np.nan)}
    yaxis = yaxis or {"data": (yd.min(), yd.max()) if yd.size else (np.nan, np.nan)}
    if "half" in xaxis and not (xaxis.get("lim") or xaxis.get("ticks")):
        from battery_analysis.plotting.axes import span_axis
        from battery_analysis.plotting.axes import format_ticks
        xlo, xhi, xt = span_axis(ref, xaxis["half"], step=xaxis["span_step"] if "span_step" in xaxis else xaxis.get("step"))
        ax.set_xlim(xlo, xhi); format_ticks(ax, "x", xt, figs.get("decimals", 1))
    else:
        xlo, xhi, _ = set_axis(ax, "x", *xaxis.get("data", (None, None)), lim=xaxis.get("lim"),
                               step=xaxis.get("step"), ticks=xaxis.get("ticks"), pad=0.03,
                               min_decimals=figs.get("decimals", 1))
    ylo, yhi, _ = set_axis(ax, "y", *yaxis.get("data", (None, None)), lim=yaxis.get("lim"),
                           step=yaxis.get("step"), ticks=yaxis.get("ticks"), pad=0.05,
                           min_decimals=figs.get("decimals", 1))

    for branch, c in (("discharge", colour), ("charge", c_charge)):
        b = pts[pts["branch"] == branch]
        ax.scatter(ref + b["eta_V"], b["log_i"], s=figs.get("marker_size", 18), color=c,
                   zorder=4, edgecolors="none")
    E = np.linspace(xlo, xhi, 400)
    eta = E - ref
    fit = state.fits.get(figs.get("fit_lines", "tafel"), {})
    if fit.get("ok") and np.isfinite(fit.get("log_i0", np.nan)):
        L0 = fit["log_i0"]
        sa, sc = _slopes(fit, T)
        for slope, sign in ((sa, 1), (sc, -1)):
            if np.isfinite(slope):
                ax.plot(E, L0 + sign * slope * eta, color=line_c, lw=0.8, ls="--", zorder=2)
        ax.axhline(L0, color=line_c, lw=0.8, ls="--", zorder=2)
    bv = state.fits.get("butler_volmer", {})
    if bv.get("ok") and figs.get("show_butler_volmer", False):
        f = FARADAY / (R_GAS * T)
        i = 10 ** bv["log_i0"] * (np.exp(bv["alpha_a"] * f * eta) - np.exp(-bv["alpha_c"] * f * eta))
        ax.plot(E, np.log10(np.abs(i) + 1e-300), color="0.25", lw=0.8, ls=":", zorder=2)
    if figs.get("show_ocv", True):
        ax.axvline(ref, color=ocv_c, lw=0.8, ls="-", zorder=1)
    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_xlabel("E (V)")
    ax.set_ylabel("log (i / A cm$^{-2}$)")
    if figs.get("label"):             # label: state -> "70"; label: sample -> "CSCY 70"
        txt = state.label if figs["label"] == "state" else f"{sample.sample} {state.label}"
        ax.text(0.04, 0.96, str(txt), transform=ax.transAxes, ha="left", va="top",
                fontsize=legend_style["fontsize"])
    return fig


def _axis_specs(samples, figs):
    """
    Scales shared by a group of figures — figures.axes: sample (default: every state of a
    sample alike), batch (every figure alike) or figure (each its own).
      x  same width and tick step everywhere, each box around its own OCV (x_scale: span,
         default), or the same absolute E limits (x_scale: absolute)
      y  the same log i limits
    xlim/xstep/xticks, ylim/ystep/yticks under figures: (or figures.per_sample.<Sample>:)
    always win. Round limits and even ticks: plotting/axes.py.
    """
    from battery_analysis.plotting.axes import from_yaml, shared_range
    mode = figs.get("axes", "batch")
    span = figs.get("x_scale", "span") == "span"
    every = [st for s in samples for st in s.states]
    specs = {}
    for s in samples:
        user = {**figs, **((figs.get("per_sample") or {}).get(s.sample) or {})}
        for st in s.states:
            group = every if mode == "batch" else (s.states if mode == "sample" else [st])
            rng = [_state_ranges(g, figs) for g in group]
            xs = {"data": shared_range(r[0] for r in rng), **from_yaml(user, "x")}
            if span:
                half = max((np.nanmax(np.abs(r[0] - g.ocv_V)) for r, g in zip(rng, group) if r[0].size),
                           default=0.05)
                xs["half"] = half * 1.04
            ys = {"data": shared_range(r[1] for r in rng), **from_yaml(user, "y")}
            specs[(s.sample, st.label)] = (xs, ys)
    return specs


def plot_summary(samples: Sequence, quantity: str, figs: dict, methods=None):
    """i0 or Rct vs state, every sample, one marker per method."""
    apply_style()
    fig, ax = plt.subplots(figsize=_size(figs.get("summary_size", "medium")))
    col = "i0_mA_cm2" if quantity == "i0" else "Rct_ohm_cm2"
    methods = list(methods or ["tafel", "butler_volmer"])
    lines = ["-", "--", ":", "-."]
    for k, s in enumerate(samples):
        r = s.results()
        c = _sample_colour(s, figs, k)
        for j, m in enumerate(methods):
            d = r[(r["method"] == m) & r["ok"]]
            if d.empty:
                continue
            mk, lab = METHOD_STYLE.get(m, ("o", m))
            x = np.asarray(d["state"], float)
            parts = ([s.sample] if len(samples) > 1 else []) + ([lab] if len(methods) > 1 else [])
            ax.plot(x, d[col], ls=lines[j % len(lines)], marker=mk, ms=3.5, lw=0.8, color=c,
                    mfc=c if j == 0 else "none", label=" ".join(parts) or None)
    ax.set_yscale("log")
    ax.set_xlabel(samples[0].settings.get("state_name", "DOD") + " (%)" if samples else "")
    ax.set_ylabel("i$_0$ (mA cm$^{-2}$)" if quantity == "i0" else "R$_{ct}$ (Ω cm$^{2}$)")
    if figs.get("summary_legend", True) and ax.get_legend_handles_labels()[1]:
        ax.legend(**legend_style, loc="best")
    return fig


def export_figures(samples: Sequence, figs: dict, out_dir: Path, batch: str, show: bool = False) -> List[Path]:
    formats = figs.get("formats", ["svg"])
    root = Path(out_dir) / batch
    written = []
    if figs.get("per_state", True):
        specs = _axis_specs(samples, figs)
        for s in samples:
            for st in s.states:
                xs, ys = specs[(s.sample, st.label)]
                fig = plot_state(s, st, figs, _sample_colour(s, figs, samples.index(s)), xs, ys)
                written += _save(fig, root / s.sample / batch_filename(f"{s.sample}_{s.settings.get('state_name', 'DOD')}{st.label}", batch), formats)
                plt.show() if show else plt.close(fig)
    for q in ("i0", "Rct"):
        fig = plot_summary(samples, q, figs, figs.get("summary_methods"))
        written += _save(fig, root / batch_filename(f"Tafel_{q}", batch), formats)
        plt.show() if show else plt.close(fig)
    return written
