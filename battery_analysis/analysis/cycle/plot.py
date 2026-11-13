"""
Cycle plotting: capacity vs cycle number, and CE vs cycle number.
Styling (SCATTER, LINE, SAVE_DPI, legend_style) comes from
plotting/style.py, matching the original plot_capacity_ce.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, SCATTER, CE_SCATTER, LINE, SAVE_DPI,
    figures_dir,
)

apply_style()


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "Cycle", batch_name)


def plot_capacity(config, cycle_cfg, repo, project_id: int, batch_name=None, save="Capacity.svg") -> None:
    """Discharge capacity vs cycle number, one line per sample's representative replicate."""
    # repo.list_samples() is project-wide (every sample any technique has
    # ever registered) — filter to samples that actually have a Cycle
    # result in this batch, same fix already applied in eis/drt, so an
    # unrelated technique/batch's sample doesn't get iterated for nothing.
    experiments_df = repo.list_cycle_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    plot_cfg = cycle_cfg.capacity_plot

    fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
    fig.patch.set_alpha(0)

    for zorder, sample in enumerate(samples_df.itertuples(), start=1):
        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        points = repo.load_cycle_points(int(rows.iloc[0]["experiment_id"]))
        if points.empty:
            continue
        ax.plot(points["cycle_number"], points["capacity_discharge"], color=sample.color, **LINE, zorder=zorder)
        ax.scatter(
            points["cycle_number"], points["capacity_discharge"], s=SCATTER["marker_size"],
            color=sample.color, edgecolors=SCATTER["solid_edge_color"], linewidth=SCATTER["solid_edge_width"],
            label=sample.name, zorder=zorder + 0.1,
        )

    ax.set_xlabel("Cycle No.")
    ax.set_ylabel("Capacity (mAh/g)")
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=plot_cfg.legend_loc, **legend_style)
    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), save, batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)


def plot_ce(config, cycle_cfg, repo, project_id: int, batch_name=None, save="CE.svg") -> None:
    """Coulombic efficiency vs cycle number."""
    # repo.list_samples() is project-wide (every sample any technique has
    # ever registered) — filter to samples that actually have a Cycle
    # result in this batch, same fix already applied in eis/drt, so an
    # unrelated technique/batch's sample doesn't get iterated for nothing.
    experiments_df = repo.list_cycle_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    plot_cfg = cycle_cfg.ce_plot

    fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
    fig.patch.set_alpha(0)

    for zorder, sample in enumerate(samples_df.itertuples(), start=1):
        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        points = repo.load_cycle_points(int(rows.iloc[0]["experiment_id"]))
        if points.empty:
            continue
        ax.scatter(
            points["cycle_number"], points["ce_pct"], s=CE_SCATTER["marker_size"],
            color=sample.color, edgecolors="none", label=sample.name, zorder=zorder,
        )

    ax.set_xlabel("Cycle No.")
    ax.set_ylabel("CE (%)")
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=plot_cfg.legend_loc, **legend_style)
    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), save, batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)


def plot_formation(config, cycle_cfg, repo, project_id: int, batch_name=None) -> None:
    """
    ONE figure comparing every sample's formation cycle, voltage vs
    capacity — legend entries are sample names (e.g. Bare, CFLMP, MXLMP).
    Which single cycle gets drawn is profiles.formation_plot_cycle (falls
    back to the first entry in profiles.formation_cycles) — see the
    per-sample charge/discharge/CE numbers import_cycle_profiles() prints
    for each formation cycle to help you pick whichever looks cleanest.
    """
    from battery_analysis.analysis.cycling_common.profile_plot import plot_formation_comparison

    pick = cycle_cfg.profiles
    cycle_num = pick.formation_plot_cycle or (pick.formation_cycles[0] if pick.formation_cycles else None)
    if cycle_num is None:
        print("⚠️  No formation_cycles set under profiles: in cycle.yaml — nothing to plot.")
        return

    # repo.list_samples() is project-wide (every sample any technique has
    # ever registered) — filter to samples that actually have a Cycle
    # result in this batch, same fix already applied in eis/drt, so an
    # unrelated technique/batch's sample doesn't get iterated for nothing.
    experiments_df = repo.list_cycle_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    plot_formation_comparison(
        config, _figures_dir(config, batch_name), repo, samples_df, experiments_df,
        cycle_num=cycle_num, modes=pick.modes, plot_cfg=cycle_cfg.formation_plot,
        save=cycle_cfg.formation_plot.save or "Formation.svg", batch=batch_name)
    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")


def plot_profiles(config, cycle_cfg, repo, project_id: int, batch_name=None) -> None:
    """Voltage-vs-capacity profiles for this batch's profiles.custom_cycles (see cycle.yaml)."""
    from battery_analysis.analysis.cycling_common.profile_plot import plot_profile

    # repo.list_samples() is project-wide (every sample any technique has
    # ever registered) — filter to samples that actually have a Cycle
    # result in this batch, same fix already applied in eis/drt, so an
    # unrelated technique/batch's sample doesn't get iterated for nothing.
    experiments_df = repo.list_cycle_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    plot_profile(
        config, _figures_dir(config, batch_name), repo, samples_df, experiments_df,
        cycles=cycle_cfg.profiles.custom_cycles, modes=cycle_cfg.profiles.modes,
        plot_cfg=cycle_cfg.profiles_plot,
        save=cycle_cfg.profiles_plot.save or "{sample}_profiles.svg", batch=batch_name)
    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")


def run_plot(config, cycle_cfg, repo, project_id: int, batch_name=None) -> None:
    plot_capacity(config, cycle_cfg, repo, project_id, batch_name=batch_name)
    plot_ce(config, cycle_cfg, repo, project_id, batch_name=batch_name)
    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")
