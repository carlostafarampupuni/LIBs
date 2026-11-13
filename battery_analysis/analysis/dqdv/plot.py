"""dQ/dV plotting: overlay dQ/dV vs voltage for a sample's configured cycles."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, SAVE_DPI, figures_dir,
)
from battery_analysis.analysis.cycling_common.io import ordinal

apply_style()


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "dQdV", batch_name)


def plot_entry(config, dqdv_cfg, repo, project_id: int, entry, batch_name=None) -> None:
    experiments_df = repo.list_dqdv_experiments(project_id, batch_name=batch_name)
    rows = experiments_df[experiments_df["sample"] == entry.sample]
    if rows.empty:
        print(f"  ! no dQ/dV experiment for sample {entry.sample!r}")
        return
    experiment_id = int(rows.iloc[0]["experiment_id"])

    cycles = entry.cycles or sorted(repo.list_dqdv_cycles(experiment_id)["cycle_number"].unique())
    modes = entry.modes or ["charge", "discharge"]

    fig, ax = plt.subplots(figsize=fig_sizes[dqdv_cfg.plotting.figsize])
    fig.patch.set_alpha(0)

    # One color PER CYCLE (not per cycle*mode) — charge and discharge for
    # the same cycle share a color and a single ordinal-form legend entry
    # ("3rd" rather than "Cycle 3 (charge)" + a separate "Cycle 3
    # (discharge)"), set on whichever of the two draws first.
    n_cycles = max(len(cycles), 1)
    colors = plt.cm.viridis([i / max(n_cycles - 1, 1) for i in range(n_cycles)])
    for color, cycle in zip(colors, cycles):
        labeled = False
        for mode in modes:
            curve = repo.load_dqdv_curve(experiment_id, cycle, mode)
            if curve.empty:
                continue
            label = ordinal(cycle) if not labeled else None
            ax.plot(curve["voltage"], curve["dqdv"], color=color, lw=1.0, label=label)
            labeled = True

    ax.axhline(0, color="black", lw=0.5, ls="--", alpha=0.3)
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("dQ/dV (mAh/g/V)")
    format_axes(ax, entry.xlim, entry.ylim,
                xtick_step=entry.xtick_step, ytick_step=entry.ytick_step,
                xticks_n=entry.xticks_n, yticks_n=entry.yticks_n)
    # dQ/dV magnitude isn't meaningful to read off directly here (peak
    # POSITION is the point) — hide the y-axis's values/ticks, keep the
    # spine and label so it's still clear what the axis represents.
    ax.set_yticks([])
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=dqdv_cfg.plotting.legend_loc, fontsize=7)
    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), (entry.save or f"{entry.sample}_dqdv.svg"), batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)


def run_plot(config, dqdv_cfg, repo, project_id: int, batch_name=None) -> None:
    for entry in dqdv_cfg.plotting.entries:
        plot_entry(config, dqdv_cfg, repo, project_id, entry, batch_name=batch_name)
    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")
