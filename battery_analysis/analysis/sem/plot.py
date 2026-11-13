"""
SEM plots — bar charts (contact area, interface quality, gap thickness,
porosity, mean particle size) and per-sample particle-size histograms,
reading straight from the database. Mirrors the original sem.py's
plot_cross_section_*/plot_surface_*/plot_powder_* functions, but each
sample's value is the MEAN across every imported replicate image for
that sample+role (the original script had exactly one image per sample;
real batches often have several, so averaging is the more honest
default rather than picking one arbitrarily).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, BAR, SAVE_DPI, figures_dir,
)

apply_style()


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "SEM", batch_name)


def _sample_color(config, sample: str, fallback_idx: int) -> str:
    from battery_analysis.discovery import color_for
    override = config.sample_override(sample)
    if override and override.color:
        return override.color
    return color_for(fallback_idx, config.palette or None)


def _role_experiments(repo, project_id: int, batch_name, role: str) -> pd.DataFrame:
    df = repo.list_sem_experiments(project_id, batch_name=batch_name, role=role)
    return df


def sample_metric_means(repo, project_id: int, batch_name, role: str) -> pd.DataFrame:
    """One row per sample: mean of every metric across all imported images
    for that sample+role. Empty DataFrame if this role has nothing imported."""
    exps = _role_experiments(repo, project_id, batch_name, role)
    if exps.empty:
        return exps
    rows = []
    for _, row in exps.iterrows():
        metrics = repo.load_sem_metrics(int(row["experiment_id"]))
        metrics["Sample"] = row["sample"]
        rows.append(metrics)
    df = pd.DataFrame(rows)
    return df.groupby("Sample", as_index=False).mean(numeric_only=True)


def pooled_diameters(repo, project_id: int, batch_name, role: str, sample: str) -> np.ndarray:
    exps = _role_experiments(repo, project_id, batch_name, role)
    exps = exps[exps["sample"] == sample]
    all_diam = [repo.load_sem_array(int(r["experiment_id"]), "particle_diameters") for _, r in exps.iterrows()]
    all_diam = [d for d in all_diam if len(d)]
    return np.concatenate(all_diam) if all_diam else np.array([])


def _bar_plot(config, means_df: pd.DataFrame, column: str, ylabel: str, save_name: str,
              xlim=None, ylim=(0, 100), xticks=None, yticks=None, fmt="{:.1f}", batch_name=None):
    if means_df.empty or column not in means_df.columns:
        print(f"⚠️  No SEM data for {ylabel!r} — nothing to plot.")
        return
    fig, ax = plt.subplots(figsize=fig_sizes["small"])
    samples = means_df["Sample"].tolist()
    values = means_df[column].tolist()
    colors = [_sample_color(config, s, i) for i, s in enumerate(samples)]

    bars = ax.bar(samples, values, color=colors, **BAR)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val + (0.02 * (ylim[1] - ylim[0]) if ylim else 1),
                fmt.format(val), ha="center", va="bottom", fontweight="bold", fontsize=9)

    ax.set_ylabel(ylabel)
    format_axes(ax, xlim=xlim, ylim=ylim, xticks=xticks, yticks=yticks)
    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), save_name, batch_name), dpi=SAVE_DPI, bbox_inches="tight")
    finish_figure(fig)


def plot_cross_section_contact(config, repo, project_id, batch_name=None, ylim=(0, 100), yticks=None):
    means = sample_metric_means(repo, project_id, batch_name, "cross_section")
    _bar_plot(config, means, "contact_area_pct", "Contact Area (%)", "CrossContact.svg",
              ylim=ylim, yticks=yticks, fmt="{:.1f}%", batch_name=batch_name)


def plot_cross_section_quality(config, repo, project_id, batch_name=None, ylim=(0, 100), yticks=None):
    means = sample_metric_means(repo, project_id, batch_name, "cross_section")
    _bar_plot(config, means, "quality_score", "Interface Quality", "InterfaceQuality.svg", ylim=ylim, yticks=yticks, batch_name=batch_name)


def plot_cross_section_gap(config, repo, project_id, batch_name=None, ylim=None, yticks=None):
    means = sample_metric_means(repo, project_id, batch_name, "cross_section")
    _bar_plot(config, means, "mean_gap_um", "Mean Gap (µm)", "Gap.svg", ylim=ylim, yticks=yticks, fmt="{:.2f}", batch_name=batch_name)


def plot_surface_porosity(config, repo, project_id, batch_name=None, ylim=(0, 100), yticks=None):
    means = sample_metric_means(repo, project_id, batch_name, "surface")
    _bar_plot(config, means, "porosity_pct", "Porosity (%)", "Porosity.svg", ylim=ylim, yticks=yticks, fmt="{:.1f}%", batch_name=batch_name)


def plot_surface_particles(config, repo, project_id, batch_name=None, xlim=None, ylim=None, xticks=None, yticks=None):
    exps = _role_experiments(repo, project_id, batch_name, "surface")
    if exps.empty:
        print("⚠️  No SEM surface data — nothing to plot.")
        return
    for i, sample in enumerate(sorted(exps["sample"].unique())):
        diam = pooled_diameters(repo, project_id, batch_name, "surface", sample)
        if len(diam) == 0:
            continue
        fig, ax = plt.subplots(figsize=fig_sizes["medium"])
        ax.hist(diam, bins=20, alpha=0.6, color=_sample_color(config, sample, i),
                edgecolor="black", linewidth=0.8, label=sample)
        ax.set_xlabel("Particle Diameter (µm)")
        ax.set_ylabel("Frequency")
        ax.legend(**legend_style)
        format_axes(ax, xlim=xlim, ylim=ylim, xticks=xticks, yticks=yticks)
        fig.tight_layout()
        fig.savefig(batch_path(_figures_dir(config, batch_name), f"Particles_{sample}.svg", batch_name), dpi=SAVE_DPI, bbox_inches="tight")
        finish_figure(fig)


def plot_powder_particles(config, repo, project_id, batch_name=None, xlim=None, ylim=None, xticks=None, yticks=None):
    exps = _role_experiments(repo, project_id, batch_name, "powder")
    if exps.empty:
        print("⚠️  No SEM powder data — nothing to plot.")
        return
    for i, sample in enumerate(sorted(exps["sample"].unique())):
        diam = pooled_diameters(repo, project_id, batch_name, "powder", sample)
        if len(diam) == 0:
            continue
        fig, ax = plt.subplots(figsize=fig_sizes["medium"])
        ax.hist(diam, bins=30, alpha=0.6, color=_sample_color(config, sample, i),
                edgecolor="black", linewidth=0.8, label=sample)
        ax.set_xlabel("Particle Diameter (µm)")
        ax.set_ylabel("Frequency")
        ax.legend(**legend_style)
        format_axes(ax, xlim=xlim, ylim=ylim, xticks=xticks, yticks=yticks)
        fig.tight_layout()
        fig.savefig(batch_path(_figures_dir(config, batch_name), f"Powder_{sample}.svg", batch_name), dpi=SAVE_DPI, bbox_inches="tight")
        finish_figure(fig)


def plot_powder_mean_size(config, repo, project_id, batch_name=None, ylim=None, yticks=None):
    means = sample_metric_means(repo, project_id, batch_name, "powder")
    _bar_plot(config, means, "mean_particle_um", "Mean Particle Size (µm)", "MeanSize.svg", ylim=ylim, yticks=yticks, batch_name=batch_name)


def run_plot(config, sem_cfg, repo, project_id: int, batch_name=None) -> None:
    """Draw every SEM figure this batch has data for — the notebook-facing
    entry point (project.plot_sem())."""
    if sem_cfg.cross_section.enabled:
        plot_cross_section_contact(config, repo, project_id, batch_name)
        plot_cross_section_quality(config, repo, project_id, batch_name)
        plot_cross_section_gap(config, repo, project_id, batch_name)
    if sem_cfg.surface.enabled:
        plot_surface_porosity(config, repo, project_id, batch_name)
        plot_surface_particles(config, repo, project_id, batch_name)
    if sem_cfg.powder.enabled:
        plot_powder_particles(config, repo, project_id, batch_name)
        plot_powder_mean_size(config, repo, project_id, batch_name)
