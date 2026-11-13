"""RIC / RIC_disconn / RIC_sei vs cycle — one figure per metric, every
representative sample overlaid, reading straight from the database.
Mirrors the original ric.py's plot_ric()/plot_ric_disconn()/plot_ric_sei(),
styled from the project's shared plotting/style.py + this batch's own
ric.yaml axis settings instead of hardcoded xlim/ylim/ticks."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes,
    ensure_figure_transparency, finish_figure, RIC_LINE, SAVE_DPI, figures_dir,
)

apply_style()

_MARKERS = ["o", "s", "^", "D", "v", "P"]


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "RIC", batch_name)


def _representative_samples(repo, project_id: int, batch_name):
    df = repo.list_ric_experiments(project_id, batch_name=batch_name)
    return df[df["is_representative"] == 1] if not df.empty else df


def _plot_one(config, repo, project_id, batch_name, column: str, ylabel: str,
              save_default: str, plot_cfg, offset: bool = False):
    reps = _representative_samples(repo, project_id, batch_name)
    if reps.empty:
        print(f"⚠️  No representative RIC experiments for batch {batch_name!r} — nothing to plot.")
        return

    fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
    ensure_figure_transparency(fig)

    for i, (_, row) in enumerate(reps.iterrows()):
        df = repo.load_ric_points(int(row["experiment_id"]))
        df = df.dropna(subset=[column])
        if df.empty:
            continue
        cycles = df["cycle_number"].to_numpy()
        sample_override = config.sample_override(row["sample"])
        color = sample_override.color if sample_override and sample_override.color else None
        ax.plot(
            cycles, df[column].to_numpy(),
            f"{_MARKERS[i % len(_MARKERS)]}-", ms=3, color=color, label=row["sample"],
            **RIC_LINE,
        )

    ax.set_xlabel("Cycle (n)")
    ax.set_ylabel(ylabel)
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
    ax.legend(**legend_style)
    fig.tight_layout()

    save_name = plot_cfg.save or save_default
    fig.savefig(batch_path(_figures_dir(config, batch_name), save_name, batch_name), transparent=True, dpi=SAVE_DPI)
    finish_figure(fig)


def plot_ric(config, ric_cfg, repo, project_id: int, batch_name=None) -> None:
    _plot_one(config, repo, project_id, batch_name, "ric", "RIC", "RIC.svg", ric_cfg.ric_plot)


def plot_ric_disconn(config, ric_cfg, repo, project_id: int, batch_name=None) -> None:
    _plot_one(config, repo, project_id, batch_name, "ric_disconn", "RIC$_{DISCONN}$",
              "RIC_DISCONN.svg", ric_cfg.disconn_plot)


def plot_ric_sei(config, ric_cfg, repo, project_id: int, batch_name=None) -> None:
    _plot_one(config, repo, project_id, batch_name, "ric_sei", "RIC$_{SEI}$",
              "RIC_SEI.svg", ric_cfg.sei_plot)


def run_plot(config, ric_cfg, repo, project_id: int, batch_name=None) -> None:
    """Draw all three RIC figures — the notebook-facing entry point (project.plot_ric())."""
    plot_ric(config, ric_cfg, repo, project_id, batch_name)
    plot_ric_disconn(config, ric_cfg, repo, project_id, batch_name)
    plot_ric_sei(config, ric_cfg, repo, project_id, batch_name)
