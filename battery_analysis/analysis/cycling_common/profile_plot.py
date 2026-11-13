"""
Voltage-vs-capacity profile plots — shared rendering core for Cycle's and
Rate's plot_..._formation()/plot_..._profiles(). Lines are always solid —
a cycle's charge and discharge halves are the same color and just trace
out the familiar loop shape, no dashing.

Two distinct shapes, for two distinct questions:

  plot_profile() — ONE figure PER SAMPLE, overlaying several cycles for
  THAT sample (color = sample's own color, alpha faded from ALPHA_MIN
  (earliest) to ALPHA_MAX (latest) so the cycles read as a progression).
  Answers "how did this one sample change over cycling?" — used for
  plot_..._profiles() (any cycles you named in custom_cycles).

  plot_formation_comparison() — ONE figure, every sample's SAME single
  formation cycle overlaid (color = sample's own color, one legend entry
  per sample — "Bare" / "CFLMP" / "MXLMP"). Answers "how do the samples
  compare at formation?" — used for plot_..._formation().
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    fig_sizes, legend_style, format_axes, finish_figure,
    CYCLE_PROFILE_LINE, ALPHA_MIN, ALPHA_MAX, SAVE_DPI,
)
from .io import ordinal


def _cycle_alpha(idx: int, n: int) -> float:
    if n <= 1:
        return ALPHA_MAX
    return ALPHA_MIN + (ALPHA_MAX - ALPHA_MIN) * idx / (n - 1)


def plot_profile(config, figures_dir: Path, repo, samples_df, experiments_df, *,
                  cycles, modes, plot_cfg, xlabel="Capacity (mAh/g)", ylabel="Voltage (V)",
                  save: str, batch=None) -> None:
    """
    One figure per sample in samples_df, overlaying every (cycle, mode)
    pair in `cycles` x `modes` for that sample's representative
    experiment (read from `experiments_df`, already filtered to the
    right batch). `save` may contain "{sample}" for one filename per
    sample; a sample with nothing to plot (no imported points) is
    skipped — no empty figure is written for it.
    """
    figures_dir.mkdir(parents=True, exist_ok=True)
    n = len(cycles)

    for sample in samples_df.itertuples():
        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        experiment_id = int(rows.iloc[0]["experiment_id"])

        fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
        fig.patch.set_alpha(0)
        any_drawn = False

        for idx, cycle_num in enumerate(cycles):
            alpha = _cycle_alpha(idx, n)
            label_used = False
            for mode in modes:
                points = repo.load_profile_points(experiment_id, cycle_num, mode)
                if points.empty:
                    continue
                any_drawn = True
                # One legend entry per cycle, not per (cycle, mode) line —
                # both modes share color/alpha/linestyle, so a second
                # legend entry would just duplicate the first. Ordinal
                # form (1st, 2nd, 3rd, ...) to match the *_Cycle.csv
                # column naming you already see in the raw file.
                label = ordinal(cycle_num) if not label_used else None
                ax.plot(
                    points["capacity"], points["voltage"], color=sample.color,
                    linestyle="-", alpha=alpha, label=label,
                    **CYCLE_PROFILE_LINE,
                )
                label_used = True

        if not any_drawn:
            plt.close(fig)
            continue

        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                    xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                    xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
        handles, _ = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc=plot_cfg.legend_loc, **legend_style)

        fig.tight_layout()
        save_name = save.format(sample=sample.name) if "{sample}" in save else save
        fig.savefig(batch_path(figures_dir, save_name, batch), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
        finish_figure(fig)


def plot_formation_comparison(config, figures_dir: Path, repo, samples_df, experiments_df, *,
                               cycle_num, modes, plot_cfg, xlabel="Capacity (mAh/g)", ylabel="Voltage (V)",
                               save: str, batch=None) -> None:
    """
    ONE figure comparing every sample's SAME single formation cycle —
    color = each sample's own color, one legend entry per sample (e.g.
    "Bare" / "CFLMP" / "MXLMP") — same "one line per sample" convention
    as plot_capacity()/DRT's plot_comparison() elsewhere in this project.
    A sample with no imported data for `cycle_num` is silently left out
    (not an empty line) — see the console readout from
    import_..._profiles() for exactly which samples/cycles came through.
    """
    figures_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
    fig.patch.set_alpha(0)
    any_drawn = False

    for sample in samples_df.itertuples():
        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        experiment_id = int(rows.iloc[0]["experiment_id"])

        label_used = False
        for mode in modes:
            points = repo.load_profile_points(experiment_id, cycle_num, mode)
            if points.empty:
                continue
            any_drawn = True
            ax.plot(
                points["capacity"], points["voltage"], color=sample.color,
                linestyle="-", label=(sample.name if not label_used else None),
                **CYCLE_PROFILE_LINE,
            )
            label_used = True

    if not any_drawn:
        plt.close(fig)
        print(f"⚠️  No formation-cycle {cycle_num} data found for any sample "
              f"— check profiles.formation_cycles / run import_..._profiles() first.")
        return

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=plot_cfg.legend_loc, **legend_style)

    fig.tight_layout()
    fig.savefig(batch_path(figures_dir, save, batch), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)
