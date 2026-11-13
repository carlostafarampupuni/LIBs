"""FTIR spectrum plots, reading straight from the database. Mirrors the
original ftir.py's plot_ftir_sheet(), styled from ftir.yaml instead of
hardcoded call-site kwargs."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, SAVE_DPI, figures_dir,
)

apply_style()


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "FTIR", batch_name)


def _sample_color(config, sample: str, fallback_idx: int) -> str:
    from battery_analysis.discovery import color_for
    override = config.sample_override(sample)
    if override and override.color:
        return override.color
    return color_for(fallback_idx, config.palette or None)


def _plot_group(config, repo, project_id, batch_name, sheet_cfg, samples: list[str], save_name: str) -> None:
    exps = repo.list_ftir_experiments(project_id, batch_name=batch_name)
    if exps.empty:
        print(f"⚠️  No FTIR data imported for batch {batch_name!r} — nothing to plot.")
        return
    prefix = f"{sheet_cfg.sheet}:"
    exps = exps[exps["source_folder"].str.startswith(prefix)]

    fig, ax = plt.subplots(figsize=fig_sizes[sheet_cfg.plot.figsize])
    drew_any = False
    for i, sample in enumerate(samples):
        rows = exps[exps["sample"] == sample]
        if rows.empty:
            print(f"⚠️  {sample!r} not found in sheet {sheet_cfg.sheet!r} — skipping")
            continue
        df = repo.load_ftir_points(int(rows.iloc[0]["experiment_id"]), sheet_cfg.spectrum_type)
        if df.empty:
            continue
        ax.plot(df["wavenumber"], df["value"], color=_sample_color(config, sample, i), lw=1.5, label=sample)
        drew_any = True

    if not drew_any:
        plt.close(fig)
        return

    ax.set_xlabel(sheet_cfg.plot.xlabel)
    ax.set_ylabel(sheet_cfg.spectrum_type)
    format_axes(ax, sheet_cfg.plot.xlim, sheet_cfg.plot.ylim,
                xtick_step=sheet_cfg.plot.xtick_step, ytick_step=sheet_cfg.plot.ytick_step,
                xticks_n=sheet_cfg.plot.xticks_n, yticks_n=sheet_cfg.plot.yticks_n)
    if sheet_cfg.plot.invert_x:
        ax.invert_xaxis()
    if sheet_cfg.plot.hide_yaxis:
        ax.tick_params(labelleft=False)

    leg = ax.legend(loc=sheet_cfg.plot.legend_loc, **legend_style)
    for t in leg.get_texts():
        t.set_color("black")
    fig.tight_layout()

    fig.savefig(batch_path(_figures_dir(config, batch_name), save_name, batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)


def run_plot(config, ftir_cfg, repo, project_id: int, batch_name=None) -> None:
    """Draw every configured figure (sheet_cfg.figures, or one figure per
    sheet with every curve overlaid if figures: is left empty)."""
    for sheet_cfg in ftir_cfg.sheets:
        groups = sheet_cfg.figures or [[c.sample for c in sheet_cfg.curves]]
        for group in groups:
            save_name = sheet_cfg.plot.save or f"FTIR_{sheet_cfg.sheet}_{'_'.join(group)}.pdf"
            _plot_group(config, repo, project_id, batch_name, sheet_cfg, group, save_name)
