"""
EIS plotting — reads spectra/fit results from the project database
instead of an Excel file. Visual output (line widths, markers, DPI,
axis handling, gradient colours) is unchanged from the original
battery_analysis/eis.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, compute_ticks, finish_figure,
    EIS_LINE, PARAM_LINE, EIS_SCATTER, SAVE_DPI, shorten_label, figures_dir,
)
from .fitting import smooth_nyquist_data, _fix_sign_convention, model_curve

apply_style()


# =========================================================
# COLOUR GRADIENT UTILITIES (identical to original)
# =========================================================

def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rgb_to_hex(r, g, b):
    return "#{:02X}{:02X}{:02X}".format(
        int(np.clip(r, 0, 1) * 255), int(np.clip(g, 0, 1) * 255), int(np.clip(b, 0, 1) * 255),
    )


def _gradient_colors(start_hex, end_hex, n):
    if n == 1:
        return [end_hex]
    sr, sg, sb = _hex_to_rgb(start_hex)
    er, eg, eb = _hex_to_rgb(end_hex)
    return [
        _rgb_to_hex(sr + (er - sr) * i / (n - 1), sg + (eg - sg) * i / (n - 1), sb + (eb - sb) * i / (n - 1))
        for i in range(n)
    ]


def _apply_legend(ax, legend_loc, legend_kwargs):
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=legend_loc, **legend_kwargs)


def _ordered(samples_df, eis_cfg):
    """Legend order: the batch's samples: list, else by the leading number (0, 2.5, 5, 10)."""
    from battery_analysis.analysis.cv.plot import _sample_rank
    listed = getattr(eis_cfg, "samples", []) or []
    return samples_df.iloc[sorted(range(len(samples_df)), key=lambda i: _sample_rank(listed, samples_df["name"].iloc[i]))]


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "EIS", batch_name)


# =========================================================
# INDIVIDUAL NYQUIST PLOT (one cycle, all samples' representative folder)
# =========================================================

def plot_nyquist_sheet(config, eis_cfg, repo, project_id: int, sheet_name: str,
                        xlim=None, ylim=None, xticks=None, yticks=None,
                        legend_loc="best", batch_name=None) -> None:
    plot_cfg = eis_cfg.plotting
    figsize = fig_sizes[plot_cfg.figsize]

    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0.32, 0.22, 0.60, 0.65])

    measurements_df = repo.list_eis_measurements(project_id, sheet_name=sheet_name, batch_name=batch_name)
    # repo.list_samples() is project-wide (every sample any technique has
    # registered); restrict to samples with data in THIS batch so a
    # project that also holds e.g. LMR850/Bare/CFLMP from other
    # techniques doesn't iterate them here for nothing.
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(measurements_df["sample"].unique())]
    samples_df = _ordered(samples_df, eis_cfg)

    for zorder_i, sample in enumerate(samples_df.itertuples(), start=1):
        rows = measurements_df[
            (measurements_df["sample"] == sample.name)
            & (measurements_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        row = rows.iloc[0]
        spectrum = repo.load_eis_spectrum(row["measurement_id"])
        if spectrum.empty:
            continue

        zreal = spectrum["z_real"].to_numpy()
        zimag = _fix_sign_convention(spectrum["z_imag"].to_numpy())
        freqs = spectrum["freq"].to_numpy()
        mask = np.isfinite(zreal) & np.isfinite(zimag)
        if not mask.any():
            continue
        zreal, zimag, freqs = zreal[mask], zimag[mask], freqs[mask]
        Z_exp = zreal + 1j * zimag

        # A stored circuit fit is drawn as its model curve; otherwise the line is
        # the old smoothed guide (frequency extraction) or the data itself.
        fit = repo.load_eis_model_fit(row["measurement_id"]) if eis_cfg.fitting.use_model else None
        dense = np.logspace(np.log10(freqs.max()), np.log10(freqs.min()), 300)
        Z_model = model_curve(fit[0], fit[1], dense) if fit else None
        if Z_model is not None:
            Z_plot = Z_model
        elif eis_cfg.fitting.extract_at_freq and not eis_cfg.fitting.use_model:
            Z_plot = smooth_nyquist_data(Z_exp, freqs, smoothing_factor=0.15)
        else:
            Z_plot = Z_exp

        ax.plot(Z_plot.real, Z_plot.imag, "-", color=sample.color, **EIS_LINE, zorder=zorder_i)
        ax.scatter(Z_exp.real, Z_exp.imag, s=25, color=sample.color,
                   alpha=EIS_SCATTER["alpha"], label=sample.name,
                   zorder=zorder_i + 0.1, edgecolors="none")

    ax.set_xlabel("Z' (Ω)")
    ax.set_ylabel("-Z'' (Ω)")
    if xlim is not None: ax.set_xlim(xlim)
    if ylim is not None: ax.set_ylim(ylim)
    if xticks is not None: ax.set_xticks(xticks)
    if yticks is not None: ax.set_yticks(yticks)
    ax.invert_yaxis()
    ax.set_aspect("equal", adjustable="box")
    _apply_legend(ax, legend_loc, dict(legend_style))

    fig.savefig(batch_path(_figures_dir(config, batch_name), f"{sheet_name}.svg", batch_name), dpi=SAVE_DPI, transparent=True)
    finish_figure(fig)


# =========================================================
# PARAMETER EVOLUTION (Rsei / Rct / W vs cycle number)
# =========================================================

def plot_param_evolution(config, eis_cfg, repo, project_id: int, param: str, ylabel: str, *,
                          ylim=None, yticks_n=5, ytick_step=None, yscale=None, save=None,
                          xlim=None, legend_loc="best", batch_name=None) -> None:
    """
    param vs. measurement stage, one line per sample. "Stage" is this
    batch's own declared sheet_order — an ORDINAL position (1st sheet,
    2nd sheet, ...), not a value parsed out of the sheet label. That's
    deliberate: sheet labels can be anything a batch's raw data calls
    for (cycle counts, hours + a "Precycle" milestone, voltage steps,
    current densities, ...), and trying to recover a single comparable
    number from arbitrary label text is exactly what broke before —
    "3.0V"/"3.15V" both read as the digit 3, "0.25"/"0.5" both read as
    0, and a non-numeric label like "Precycle" fell out of the plot
    entirely. Ordinal position always works, for any labeling scheme,
    because it comes from the order you already declared in sheet_order
    (which every batch must define correctly anyway for import to work)
    rather than from re-parsing text meant for humans to read.
    """
    samples_df = repo.list_samples(project_id)
    palette = {s.name: s.color for s in samples_df.itertuples()}
    zorder_map = {s.name: i + 1 for i, s in enumerate(samples_df.itertuples())}

    fit_df = repo.load_eis_fit_results(project_id, batch_name=batch_name)
    if fit_df.empty or param not in fit_df.columns:
        print(f"⚠️  No fit results for parameter '{param}' — nothing to plot.")
        return

    # label -> ordinal stage index (1-based), from this batch's own
    # declared sheet_order/sheet_names — the single source of truth for
    # "what order do these measurements go in" everywhere in this file.
    stage_labels = [eis_cfg.sheet_names.get(k, k) for k in eis_cfg.sheet_order]
    stage_index = {label: i + 1 for i, label in enumerate(stage_labels)}

    fit_df = fit_df.copy()
    fit_df["StageIdx"] = fit_df["Sheet"].map(stage_index)
    unmatched = fit_df[fit_df["StageIdx"].isna()]["Sheet"].unique().tolist()
    if unmatched:
        print(f"⚠️  Sheet(s) {unmatched} aren't in this batch's sheet_order — excluded from the {param} trend.")
    fit_df = fit_df.dropna(subset=["StageIdx"]).sort_values("StageIdx")

    fig = plt.figure(figsize=fig_sizes[eis_cfg.plotting.figsize])
    ax = fig.add_axes([0.32, 0.22, 0.60, 0.65])

    for sample in fit_df["Sample"].unique():
        df_s = fit_df[fit_df["Sample"] == sample]
        z = zorder_map.get(sample, 1)
        ax.plot(df_s["StageIdx"], df_s[param], color=palette.get(sample, "#333333"), **PARAM_LINE, zorder=z)
        ax.scatter(df_s["StageIdx"], df_s[param], s=25, color=palette.get(sample, "#333333"),
                   edgecolors="#ffffff", lw=0.5, label=sample, zorder=z + 0.1)

    ax.set_xlabel("Measurement stage")
    ax.set_ylabel(ylabel)
    ax.set_xticks(range(1, len(stage_labels) + 1))
    ax.set_xticklabels([shorten_label(l) for l in stage_labels], rotation=45, ha="right", fontsize=6.5)
    if yscale: ax.set_yscale(yscale)
    if xlim is not None: ax.set_xlim(xlim)
    if ylim is not None: ax.set_ylim(ylim)
    if ylim is not None: ax.set_yticks(compute_ticks(ylim, step=ytick_step, n=yticks_n))
    _apply_legend(ax, legend_loc, dict(legend_style))

    if save:
        fig.savefig(batch_path(_figures_dir(config, batch_name), save, batch_name), dpi=SAVE_DPI, transparent=True)
    finish_figure(fig)


# =========================================================
# ALL-SAMPLES CYCLE EVOLUTION (gradient Nyquist overlay)
# =========================================================

def plot_all_cycles(config, eis_cfg, repo, project_id: int, *,
                     cycle_labels: Optional[List[str]] = None,
                     xticks_n=5, yticks_n=5, xtick_step=None, ytick_step=None,
                     legend_loc="best",
                     legend_fontsize=None, save="all_cycles_evolution.svg",
                     batch_name=None) -> None:
    plot_cfg = eis_cfg.plotting
    sheet_order = eis_cfg.sheet_order
    sheets_in_order = [eis_cfg.sheet_names.get(k, k) for k in sheet_order]
    labels = cycle_labels if cycle_labels else sheets_in_order

    evo_lookup = {e.name: e for e in plot_cfg.evolution}
    # repo.list_samples() is project-wide — restrict to samples that
    # actually have data in THIS batch, otherwise every other technique's
    # sample (with no evolution: entry here, so it silently falls back to
    # the (0,40)/(-40,0) default) gets folded into the xlim/ylim min/max
    # below, which can quietly shrink or distort the axis range for the
    # samples that are actually being plotted.
    batch_measurements_df = repo.list_eis_measurements(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(batch_measurements_df["sample"].unique())]

    all_xlim = [
        (evo_lookup[s.name].xlim if s.name in evo_lookup and evo_lookup[s.name].xlim else (0, 40))
        for s in samples_df.itertuples()
    ]
    all_ylim = [
        (evo_lookup[s.name].ylim if s.name in evo_lookup and evo_lookup[s.name].ylim else (-40, 0))
        for s in samples_df.itertuples()
    ]
    xlim = (min(x[0] for x in all_xlim), max(x[1] for x in all_xlim))
    ylim = (min(y[0] for y in all_ylim), max(y[1] for y in all_ylim))

    fig = plt.figure(figsize=fig_sizes[plot_cfg.figsize])
    ax = fig.add_axes([0.32, 0.22, 0.60, 0.65])

    for zorder_base, sample in enumerate(samples_df.itertuples(), start=1):
        evo = evo_lookup.get(sample.name)
        start = evo.gradient_start if evo else "#C0C0C0"
        end = (evo.gradient_end if evo and evo.gradient_end else sample.color)
        colors = _gradient_colors(start, end, len(sheets_in_order))

        for i, (sheet_name, color, clabel) in enumerate(zip(sheets_in_order, colors, labels)):
            measurements_df = repo.list_eis_measurements(project_id, sheet_name=sheet_name, batch_name=batch_name)
            rows = measurements_df[
                (measurements_df["sample"] == sample.name)
                & (measurements_df["is_representative"] == 1)
            ]
            if rows.empty:
                continue
            spectrum = repo.load_eis_spectrum(rows.iloc[0]["measurement_id"])
            if spectrum.empty:
                continue
            zreal = spectrum["z_real"].to_numpy()
            zimag = _fix_sign_convention(spectrum["z_imag"].to_numpy())
            mask = np.isfinite(zreal) & np.isfinite(zimag)
            if not mask.any():
                continue

            z = zorder_base + i * 0.1
            legend_label = sample.name if i == 0 else "_nolegend_"
            ax.plot(zreal[mask], zimag[mask], "-", color=color, **EIS_LINE, zorder=z)
            ax.scatter(zreal[mask], zimag[mask], s=25, color=color,
                       alpha=EIS_SCATTER["alpha"], label=legend_label,
                       zorder=z + 0.05, edgecolors="none")

    ax.set_xlabel("Z' (Ω)")
    ax.set_ylabel("-Z'' (Ω)")
    ax.set_xlim(xlim); ax.set_ylim(ylim)
    ax.set_xticks(compute_ticks(xlim, step=xtick_step, n=xticks_n))
    ax.set_yticks(compute_ticks(ylim, step=ytick_step, n=yticks_n))
    ax.invert_yaxis()
    ax.set_aspect("equal", adjustable="box")

    leg_kw = dict(legend_style)
    if legend_fontsize is not None:
        leg_kw["fontsize"] = legend_fontsize
    _apply_legend(ax, legend_loc, leg_kw)

    fig.savefig(batch_path(_figures_dir(config, batch_name), save, batch_name), dpi=SAVE_DPI, transparent=True)
    finish_figure(fig)


def run_plot(config, eis_cfg, repo, project_id: int, batch_name=None) -> None:
    """Generate every plot configured under eis_cfg.plotting, for every sheet."""
    plot_cfg = eis_cfg.plotting

    for sheet_def in eis_cfg.sheets:
        xticks = compute_ticks(sheet_def.xlim, step=sheet_def.xtick_step, n=sheet_def.xticks_n or 5)
        yticks = compute_ticks(sheet_def.ylim, step=sheet_def.ytick_step, n=sheet_def.yticks_n or 5)
        plot_nyquist_sheet(
            config, eis_cfg, repo, project_id, sheet_def.sheet,
            xlim=sheet_def.xlim, ylim=sheet_def.ylim, xticks=xticks, yticks=yticks,
            legend_loc=plot_cfg.legend_loc, batch_name=batch_name,
        )

    for p in plot_cfg.param_plots:
        plot_param_evolution(
            config, eis_cfg, repo, project_id, p.param, p.ylabel,
            ylim=p.ylim, yticks_n=p.yticks_n, ytick_step=p.ytick_step, yscale=p.yscale, save=p.save,
            xlim=plot_cfg.axis_defaults.xlim, legend_loc=plot_cfg.legend_loc, batch_name=batch_name,
        )

    if plot_cfg.evolution:
        sheet_order = eis_cfg.sheet_order
        human_labels = (
            ["Formation"] + [k + "th" for k in sheet_order[1:]] if sheet_order else None
        )
        # save filename includes batch_name so running plot_eis() for
        # several batches in one notebook session (e.g. Batch1..Batch6)
        # doesn't have each later batch silently overwrite the previous
        # one's evolution figure — they used to all land on the same
        # fixed "all_cycles_evolution.svg".
        evolution_save = (
            f"all_cycles_evolution_{batch_name}.svg" if batch_name else "all_cycles_evolution.svg"
        )
        plot_all_cycles(
            config, eis_cfg, repo, project_id,
            cycle_labels=human_labels, legend_loc=plot_cfg.legend_loc, legend_fontsize=9,
            xtick_step=plot_cfg.axis_defaults.xtick_step, xticks_n=plot_cfg.axis_defaults.xticks_n or 5,
            save=evolution_save, batch_name=batch_name,
        )

    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")
