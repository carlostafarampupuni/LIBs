"""
DRT plotting — multi-sample overlay at one EIS sheet/cycle, ported from
plot_drt_comparison in the original battery_analysis/drt.py, plus both
contour-heatmap variants (plot_contour / plot_contour_comparison) ported
from DRTAnalyzer.plot_contour / plot_contour_comparison. Reads from the
database instead of DRTAnalyzer objects held in memory.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter, zoom

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, compute_ticks, finish_figure, EIS_LINE, SAVE_DPI,
    shorten_label, figures_dir,
)

apply_style()


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "DRT", batch_name)


def _add_freq_axis(ax, xticks=None, show=True):
    """Secondary top axis showing frequency (Hz) for the log(tau) ticks below."""
    if not show:
        return
    ax2 = ax.secondary_xaxis("top", functions=(lambda x: x, lambda x: x))
    ticks = xticks if xticks is not None else ax.get_xticks()
    ax2.set_xticks(ticks)
    # ax2.set_xticklabels([f"{1.0 / (2 * np.pi * 10 ** t):.2g}" for t in ticks], fontsize=7)
    ax2.set_xticklabels([f"{1.0 / (2 * np.pi * 10 ** t):.1e}".replace("e+0", "e").replace("e-0", "e-") for t in ticks],
                        fontsize=7)
    ax2.set_xlabel("Frequency (Hz)", fontsize=8)


def plot_comparison(config, drt_cfg, repo, project_id: int, sheet_name: str, *,
                     xlim=None, ylim=None, xtick_step=None, ytick_step=None,
                     xticks_n=None, yticks_n=None, peak_labels=None,
                     legend_loc="best", save=None, batch_name=None,
                     figsize=None) -> None:
    """Overlay every sample's DRT spectrum at one EIS sheet (e.g. 'EIS1')."""
    # repo.list_samples() is project-WIDE — every sample any technique has
    # ever registered (LMR850, Bare, CFLMP, ... as well as ADD/DEE), since
    # samples are shared once across the whole project, not per-batch. This
    # plot is scoped to one batch's DRT results, so restrict the loop to
    # samples that actually HAVE a result in batch_name — otherwise every
    # other technique's/batch's sample prints a spurious
    # "no DRT result for sheet ..." here, even though nothing is wrong.
    results_df = repo.list_drt_results(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(results_df["sample"].unique())]

    # figsize=None means "not overridden for this sheet" — fall back to
    # the batch-level default, same as before this option existed.
    fig, ax = plt.subplots(figsize=fig_sizes[figsize or drt_cfg.plotting.figsize])
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)

    best_peaks = None
    for sample in samples_df.itertuples():
        rows = results_df[(results_df["sample"] == sample.name) & (results_df["sheet_name"] == sheet_name)]
        if rows.empty:
            print(f"  ! {sample.name}: no DRT result for sheet '{sheet_name}'")
            continue
        result_id = int(rows.iloc[0]["result_id"])
        curve = repo.load_drt_curve(result_id)
        peaks = repo.load_drt_peaks(result_id)

        ax.plot(np.log10(curve["tau"]), curve["gamma"], color=sample.color, **EIS_LINE, label=sample.name)
        if best_peaks is None or len(peaks) > len(best_peaks):
            best_peaks = peaks

    if best_peaks is not None and len(best_peaks) and peak_labels:
        y_max = ylim[1] if ylim else 20
        for j, p in enumerate(best_peaks.itertuples()):
            ax.axvline(p.log_tau, color="black", ls="--", lw=0.8, alpha=0.4)
            if j < len(peak_labels):
                ax.text(p.log_tau, y_max * 0.92, peak_labels[j], ha="center", va="top",
                        fontsize=7.5, color="black", fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", alpha=0.6, edgecolor="none"))

    ax.axhline(0, color="black", lw=0.5, ls="--", alpha=0.3)
    ax.set_xlabel("Log(τ) / s")
    ax.set_ylabel("γ(τ) / Ω")
    xticks = compute_ticks(xlim, step=xtick_step, n=xticks_n)
    yticks = compute_ticks(ylim, step=ytick_step, n=yticks_n)
    if xlim is not None: ax.set_xlim(xlim)
    if ylim is not None: ax.set_ylim(ylim)
    if xticks is not None: ax.set_xticks(xticks)
    if yticks is not None: ax.set_yticks(yticks)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=legend_loc, **legend_style)
    _add_freq_axis(ax, xticks if xticks is not None else ax.get_xticks())

    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), (save or f"{sheet_name}_comparison.svg"), batch_name), dpi=SAVE_DPI, transparent=True)
    finish_figure(fig)


def _stack_curves_on_common_tau(curves, n_tau=None):
    """
    Combine several DRT curves (each its own tau grid) into one 2-D
    gamma matrix G[row, :] sharing a single tau axis, suitable for
    pcolormesh.

    Why this exists: compute_drt() builds tau from EACH sheet's own
    frequency sweep (`ln_tau` spans `freq.min()`..`freq.max()` for
    THAT measurement — see analysis/drt/fitting.py). Two sheets
    measured over different frequency ranges (e.g. an early-stage
    sweep that stopped at a higher minimum frequency than a later
    low-frequency Warburg-tail sweep) end up with genuinely different
    tau grids — different min, different max, not just different
    values at the same positions. The previous code took only
    `curves[0]["tau"]` and reused it as the x-axis for every row,
    which is wrong two ways at once: any row whose real tau range
    extends further than curves[0]'s gets silently clipped out of the
    heatmap (this is why combined per-sample contours were only ever
    showing color out to whatever the FIRST sheet in sheet_order
    happened to reach), and every other row's gamma values are plotted
    at tau positions that were never actually theirs.

    Fix: resample every curve onto one shared, log-uniform tau grid
    spanning the UNION of every curve's own tau range (interpolating
    in log10(tau), since that's the domain the DRT solve itself is
    uniform in), filling 0 outside a curve's own original support —
    gamma is 0 there anyway (no relaxation process was resolved
    outside the frequencies actually measured), so this doesn't invent
    signal, it just means a row whose sweep didn't reach as far
    legitimately shows less color on that side, which is the honest
    picture of the data rather than an axis clipped to one row.
    """
    log_tau_min = min(np.log10(c["tau"].to_numpy()).min() for c in curves)
    log_tau_max = max(np.log10(c["tau"].to_numpy()).max() for c in curves)
    n = n_tau or max(len(c) for c in curves)
    log_tau_common = np.linspace(log_tau_min, log_tau_max, n)

    G = np.array([
        np.interp(
            log_tau_common,
            np.log10(c["tau"].to_numpy()),
            c["gamma"].to_numpy(),
            left=0.0, right=0.0,
        )
        for c in curves
    ])
    return 10 ** log_tau_common, G


def _render_contour(G, tau, y_tick_positions, y_tick_labels, config, *,
                     figsize="medium", xlim=None, xticks=None,
                     xtick_step=None, xticks_n=None, clim=None,
                     colorbar=True, show_freq_axis=True, save=None, batch_name=None):
    """Shared rendering core for both contour variants — ported verbatim
    (zoom + gaussian_filter smoothing, jet colormap, gouraud shading)."""
    n_rows = G.shape[0]
    if n_rows == 1:
        G = np.repeat(G, 60, axis=0)
        y = np.linspace(0, 1, G.shape[0])
    else:
        G = zoom(G, zoom=[4, 1], order=min(3, n_rows - 1))
        y = np.linspace(0, n_rows - 1, G.shape[0])

    G = gaussian_filter(G, sigma=[1.0, 1.5])
    G = np.maximum(G, 0)

    vmin = clim[0] if clim else 0
    vmax = clim[1] if clim else G.max() * 0.95

    fig, ax = plt.subplots(figsize=fig_sizes[figsize])
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)

    x = np.log10(tau)
    cp = ax.pcolormesh(x, y, G, cmap="jet", vmin=vmin, vmax=vmax, shading="gouraud")

    if colorbar:
        cb = plt.colorbar(cp, ax=ax, pad=0.02, aspect=20)
        cb.set_label("γ(τ) / Ω", fontsize=9)
        cb.ax.tick_params(labelsize=7, colors="black")
        cb.outline.set_linewidth(0.8)
        cb.set_ticks([])
        # if clim:
        #     cb.set_ticks(np.linspace(vmin, vmax, 8))

    if n_rows == 1:
        ax.set_yticks([1])
        ax.set_yticklabels(y_tick_labels, fontsize=8)
    else:
        ax.set_yticks(y_tick_positions)
        ax.set_yticklabels(y_tick_labels, fontsize=8)

    ax.set_xlabel("Log(τ) / s")
    ax.grid(False)
    format_axes(ax, xlim, None, xticks, None, xtick_step=xtick_step, xticks_n=xticks_n)
    _add_freq_axis(ax, xticks, show=show_freq_axis)

    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), (save or "contour.svg"), batch_name), dpi=SAVE_DPI, transparent=True)
    finish_figure(fig)


def plot_contour_per_sample(config, drt_cfg, repo, project_id: int, sample_name: str, *,
                             sheet_order=None, batch_name=None, figsize="medium",
                             xlim=None, xticks=None, xtick_step=None, xticks_n=None,
                             clim=None, save=None) -> None:
    """
    DRT heatmap for ONE sample across its cycles — rows = cycle labels,
    x = log(tau), color = gamma. Ported from DRTAnalyzer.plot_contour.
    """
    results_df = repo.list_drt_results(project_id, batch_name=batch_name)
    rows = results_df[results_df["sample"] == sample_name]
    if sheet_order:
        rows = rows.set_index("sheet_name").reindex(sheet_order).dropna(subset=["result_id"]).reset_index()
    if rows.empty:
        print(f"  ! no DRT results for sample {sample_name!r}")
        return

    curves = [repo.load_drt_curve(int(r.result_id)) for r in rows.itertuples()]
    tau, G = _stack_curves_on_common_tau(curves)
    # Row labels are free-text sheet names (whatever a batch's YAML puts
    # in sheet_names:) so they can be wildly different lengths — shorten
    # for display only, so a row like "Precycle" doesn't blow out the
    # heatmap's left margin next to short ones like "0h"/"6h".
    labels = [shorten_label(l) for l in rows["sheet_name"]]

    _render_contour(
        G, tau, np.linspace(0, len(labels) - 1, len(labels)), labels, config,
        figsize=figsize, xlim=xlim, xticks=xticks, xtick_step=xtick_step, xticks_n=xticks_n, clim=clim,
        save=save or f"{sample_name}_contour.svg", batch_name=batch_name,
    )


def plot_contour_per_cycle(config, drt_cfg, repo, project_id: int, sheet_name: str, *,
                            batch_name=None, figsize="medium",
                            xlim=None, xticks=None, xtick_step=None, xticks_n=None,
                            clim=None, save=None) -> None:
    """
    DRT heatmap comparing SAMPLES at one cycle — rows = sample names,
    x = log(tau), color = gamma. Ported from plot_contour_comparison.
    """
    results_df = repo.list_drt_results(project_id, batch_name=batch_name)
    rows = results_df[results_df["sheet_name"] == sheet_name]
    if rows.empty:
        print(f"  ! no DRT results for sheet {sheet_name!r}")
        return

    curves = [repo.load_drt_curve(int(r.result_id)) for r in rows.itertuples()]
    tau, G = _stack_curves_on_common_tau(curves)
    labels = list(rows["sample"])

    _render_contour(
        G, tau, np.linspace(0, len(labels) - 1, len(labels)), labels, config,
        figsize=figsize, xlim=xlim, xticks=xticks, xtick_step=xtick_step, xticks_n=xticks_n, clim=clim,
        save=save or f"{sheet_name}_contour_by_sample.svg", batch_name=batch_name,
    )


def run_plot(config, drt_cfg, repo, project_id: int, batch_name=None) -> None:
    """Generate every DRT plot configured under drt_cfg.plotting — comparison overlays and heatmaps."""
    for sheet_cfg in drt_cfg.plotting.sheets:
        plot_comparison(
            config, drt_cfg, repo, project_id, sheet_cfg.sheet,
            xlim=sheet_cfg.xlim, ylim=sheet_cfg.ylim,
            xtick_step=sheet_cfg.xtick_step, ytick_step=sheet_cfg.ytick_step,
            xticks_n=sheet_cfg.xticks_n, yticks_n=sheet_cfg.yticks_n,
            peak_labels=sheet_cfg.peak_labels,
            legend_loc=drt_cfg.plotting.legend_loc, save=sheet_cfg.save, batch_name=batch_name,
            figsize=sheet_cfg.figsize,
        )

    samples_df = repo.list_samples(project_id)
    for contour_cfg in drt_cfg.plotting.contour_per_sample:
        plot_contour_per_sample(
            config, drt_cfg, repo, project_id, contour_cfg.sample,
            sheet_order=contour_cfg.sheet_order or None, batch_name=batch_name,
            figsize=contour_cfg.figsize, xlim=contour_cfg.xlim,
            xtick_step=contour_cfg.xtick_step, xticks_n=contour_cfg.xticks_n,
            clim=contour_cfg.clim, save=contour_cfg.save,
        )
    for contour_cfg in drt_cfg.plotting.contour_per_cycle:
        plot_contour_per_cycle(
            config, drt_cfg, repo, project_id, contour_cfg.sheet,
            batch_name=batch_name, figsize=contour_cfg.figsize,
            xlim=contour_cfg.xlim, xtick_step=contour_cfg.xtick_step, xticks_n=contour_cfg.xticks_n,
            clim=contour_cfg.clim, save=contour_cfg.save,
        )

    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")
