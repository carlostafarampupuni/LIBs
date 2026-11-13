"""
Rate plotting: capacity vs Rate_Cycle with vertical dotted lines marking
each C-rate step, and CE vs Rate_Cycle. draw_c_rate_lines is ported
verbatim from the original battery_analysis/rate.py.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from battery_analysis.analysis.cycling_common.discovery import representative_file
from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, SCATTER, CE_SCATTER, LINE, SAVE_DPI,
    figures_dir,
)

apply_style()


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "Rate", batch_name)


def _shown(samples_df, rate_cfg):
    """rate.yaml samples: — keep only the listed samples (empty list = all)."""
    if not rate_cfg.samples:
        return samples_df
    return samples_df[samples_df["name"].isin(rate_cfg.samples)]


def draw_c_rate_lines(ax, c_rate_lines) -> None:
    """Vertical dotted lines marking where the C-rate changes."""
    if c_rate_lines:
        style = dict(color="#646464", linestyle=":", linewidth=1, zorder=0)
        for x in c_rate_lines:
            ax.axvline(x, **style)


def _resolve_c_rate_lines(rate_cfg, max_cycle: int) -> list:
    if rate_cfg.c_rate_lines:
        return rate_cfg.c_rate_lines
    if rate_cfg.c_rate_step:
        return list(np.arange(rate_cfg.c_rate_step, max_cycle, rate_cfg.c_rate_step))
    return []


def _formation_count(rate_cfg) -> int:
    return len(rate_cfg.profiles.formation_cycles)


def _display_cycles(points, formation_count: int):
    """
    rate_point.rate_cycle_index is 0-indexed and sequential from the raw
    file's row order — NOT the same 1-indexed ordinal numbering
    profiles.formation_cycles/custom_cycles use. With formation_count > 0
    (i.e. profiles.formation_cycles is set), this drops those first
    formation_count rows and renumbers what's left so the cycle right
    after formation reads as "cycle 1" on the axis — e.g. formation_count
    2 means the 3rd raw row (index 2) displays as cycle 1 — which is what
    keeps formation cycles from cluttering this plot. With
    formation_count == 0 (nothing configured) the index is used exactly
    as before, unchanged, for full backward compatibility.
    """
    if formation_count <= 0:
        return points.assign(display_cycle=points["rate_cycle_index"])
    filtered = points[points["rate_cycle_index"] >= formation_count].copy()
    filtered["display_cycle"] = filtered["rate_cycle_index"] - formation_count + 1
    return filtered


def plot_capacity(config, rate_cfg, repo, project_id: int, batch_name=None, save="Rate.svg") -> None:
    # repo.list_samples() is project-wide — filter to samples that actually
    # have a Rate result in this batch (same fix already applied in eis/drt).
    experiments_df = repo.list_rate_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    samples_df = _shown(samples_df, rate_cfg)
    plot_cfg = rate_cfg.capacity_plot
    formation_count = _formation_count(rate_cfg)

    fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
    fig.patch.set_alpha(0)

    max_cycle = 0
    for zorder, sample in enumerate(samples_df.itertuples(), start=1):
        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        points = repo.load_rate_points(int(rows.iloc[0]["experiment_id"]))
        if points.empty:
            continue
        points = _display_cycles(points, formation_count)
        if points.empty:
            continue
        max_cycle = max(max_cycle, int(points["display_cycle"].max()))
        ax.plot(points["display_cycle"], points["capacity_discharge"], color=sample.color, **LINE, zorder=zorder)
        ax.scatter(
            points["display_cycle"], points["capacity_discharge"], s=SCATTER["marker_size"],
            color=sample.color, edgecolors=SCATTER["solid_edge_color"], linewidth=SCATTER["solid_edge_width"],
            label=sample.name, zorder=zorder + 0.1,
        )

    draw_c_rate_lines(ax, _resolve_c_rate_lines(rate_cfg, max_cycle))

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


def plot_ce(config, rate_cfg, repo, project_id: int, batch_name=None, save="Rate_CE.svg") -> None:
    # repo.list_samples() is project-wide — filter to samples that actually
    # have a Rate result in this batch (same fix already applied in eis/drt).
    experiments_df = repo.list_rate_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    samples_df = _shown(samples_df, rate_cfg)
    plot_cfg = rate_cfg.ce_plot
    formation_count = _formation_count(rate_cfg)

    fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
    fig.patch.set_alpha(0)

    max_cycle = 0
    for zorder, sample in enumerate(samples_df.itertuples(), start=1):
        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            continue
        points = repo.load_rate_points(int(rows.iloc[0]["experiment_id"]))
        if points.empty:
            continue
        points = _display_cycles(points, formation_count)
        if points.empty:
            continue
        max_cycle = max(max_cycle, int(points["display_cycle"].max()))
        ax.scatter(
            points["display_cycle"], points["ce_pct"], s=CE_SCATTER["marker_size"],
            color=sample.color, edgecolors="none", label=sample.name, zorder=zorder,
        )

    draw_c_rate_lines(ax, _resolve_c_rate_lines(rate_cfg, max_cycle))

    ax.set_xlabel("Cycle No.")
    ax.set_ylabel("Coulombic Efficiency (%)")
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=plot_cfg.legend_loc, **legend_style)
    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config, batch_name), save, batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)


def plot_formation(config, rate_cfg, repo, project_id: int, batch_name=None) -> None:
    """
    ONE figure comparing every sample's formation cycle, voltage vs
    capacity — legend entries are sample names (e.g. Bare, CFLMP, MXLMP).
    Which single cycle gets drawn is profiles.formation_plot_cycle (falls
    back to the first entry in profiles.formation_cycles) — see the
    per-sample charge/discharge/CE numbers import_rate_profiles() prints
    for each formation cycle to help you pick whichever looks cleanest.
    """
    from battery_analysis.analysis.cycling_common.profile_plot import plot_formation_comparison

    pick = rate_cfg.profiles
    cycle_num = pick.formation_plot_cycle or (pick.formation_cycles[0] if pick.formation_cycles else None)
    if cycle_num is None:
        print("⚠️  No formation_cycles set under profiles: in rate.yaml — nothing to plot.")
        return

    # repo.list_samples() is project-wide — filter to samples that actually
    # have a Rate result in this batch (same fix already applied in eis/drt).
    experiments_df = repo.list_rate_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    samples_df = _shown(samples_df, rate_cfg)
    plot_formation_comparison(
        config, _figures_dir(config, batch_name), repo, samples_df, experiments_df,
        cycle_num=cycle_num, modes=pick.modes, plot_cfg=rate_cfg.formation_plot,
        save=rate_cfg.formation_plot.save or "Formation.svg", batch=batch_name)
    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")


def plot_profiles(config, rate_cfg, repo, project_id: int, batch_name=None) -> None:
    """
    Voltage-vs-capacity profiles for this batch's profiles.custom_cycles
    PLUS the auto rate-capability sequence (see RateCapabilityConfig in
    schema.py — with formation_cycles: [1, 2] and interval 5, that's
    actual cycles 3, 8, 13, 18, ... i.e. cycle 3 renumbered as "cycle 1",
    then every 5 cycles after that). Recomputed the same deterministic
    way import_rate_profiles() did, from each sample's own *_Cycle.csv,
    so the cycle list plotted here always matches what was imported —
    and each sample gets its own figure since that sequence can end at a
    different cycle per sample.

    ESPEC batches (espec_channels: set) take a separate path — see
    _plot_profiles_espec — profiles.custom_cycles/rate_capability don't
    apply (see _import_rate_profiles_espec's docstring for why).
    """
    if rate_cfg.espec_channels:
        _plot_profiles_espec(config, rate_cfg, repo, project_id, batch_name=batch_name)
        return

    from battery_analysis.analysis.cycling_common.profile_plot import plot_profile
    from battery_analysis.analysis.cycling_common.discovery import discover_cycling_files
    from battery_analysis.analysis.cycling_common.profiles import rate_capability_cycles

    # repo.list_samples() is project-wide — filter to samples that actually
    # have a Rate result in this batch (same fix already applied in eis/drt).
    experiments_df = repo.list_rate_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    samples_df = _shown(samples_df, rate_cfg)

    data_dir = config.resolve(config.paths.data_root) / rate_cfg.data_subfolder
    discovered = {s.name: s for s in discover_cycling_files(data_dir)}
    pick, cap = rate_cfg.profiles, rate_cfg.rate_capability
    formation_count = len(pick.formation_cycles)
    interval = cap.interval or rate_cfg.c_rate_step or 1
    # Rate profiles are discharge-only, always — regardless of what
    # profiles.modes/rate_capability.modes are set to in YAML (those still
    # control formation and import). A rate-capability comparison is about
    # how much capacity came OUT at each step; the charge half just adds
    # clutter here.
    modes = ["discharge"]

    figures_dir = _figures_dir(config, batch_name)
    figures_dir.mkdir(parents=True, exist_ok=True)
    any_drawn = False
    for sample in samples_df.itertuples():
        disc_sample = discovered.get(sample.name)
        file = representative_file(disc_sample, experiments_df) if disc_sample else None
        if file is None or file.profile_path is None:
            continue
        auto = rate_capability_cycles(formation_count, interval, file.profile_path, cap.modes)
        cycles = sorted(set(pick.custom_cycles) | set(auto))
        if not cycles:
            continue
        any_drawn = True
        plot_profile(
            config, figures_dir, repo,
            samples_df[samples_df["name"] == sample.name], experiments_df,
            cycles=cycles, modes=modes, plot_cfg=rate_cfg.profiles_plot,
            save=rate_cfg.profiles_plot.save or "{sample}_profiles.svg", batch=batch_name)

    if not any_drawn:
        print("⚠️  No rate profile cycles to plot — check profiles.custom_cycles / rate_capability in rate.yaml.")
    else:
        print(f"✅  Figures written to {figures_dir}")


def _plot_profiles_espec(config, rate_cfg, repo, project_id: int, batch_name=None) -> None:
    """
    ESPEC counterpart to plot_profiles() — one figure per sample,
    overlaying every non-formation Rate_Cycle already imported by
    _import_rate_profiles_espec() (i.e. every rate_ptns step: 0.5C, 1C,
    3C, ... in Rate_Cycle order), discharge only, faded early-to-late
    same as the Wonatech path.

    The cycle list is read straight off the representative channel's own
    rate_summary() (excluding formation_ptn rows) rather than assumed —
    replicate counts can differ per C-rate step, and a channel that lost
    some half-cycles (see the dead-channel caveat in rate.yaml's Batch3
    comments) shouldn't produce a wrong guessed list.
    """
    from battery_analysis.analysis.cycling_common.espec_io import ESPECParser, find_espec_file
    from battery_analysis.analysis.cycling_common.profile_plot import plot_profile

    data_dir = config.resolve(config.paths.data_root) / rate_cfg.data_subfolder
    experiments_df = repo.list_rate_experiments(project_id, batch_name=batch_name)
    samples_df = repo.list_samples(project_id)
    samples_df = samples_df[samples_df["name"].isin(experiments_df["sample"].unique())]
    samples_df = _shown(samples_df, rate_cfg)

    by_sample: dict[str, list] = {}
    for ch in rate_cfg.espec_channels:
        by_sample.setdefault(ch.sample, []).append(ch)

    figures_dir = _figures_dir(config, batch_name)
    figures_dir.mkdir(parents=True, exist_ok=True)
    any_drawn = False
    for sample in samples_df.itertuples():
        channels = by_sample.get(sample.name)
        if not channels:
            continue
        rep = min(channels, key=lambda c: c.replicate)
        if rep.active_mass_g is None:
            continue
        raw_path = find_espec_file(data_dir, rep.channel)
        if raw_path is None:
            continue

        parser = ESPECParser(
            raw_path, active_mass_g=rep.active_mass_g,
            formation_ptn=rep.formation_ptn, rate_ptns=rep.rate_ptns,
        )
        rate_df = parser.rate_summary(include_formation=True)
        if rate_df.empty:
            continue
        cycles = sorted(int(c) for c in rate_df.loc[rate_df["Ptn No."] != rep.formation_ptn, "Rate_Cycle"])
        if not cycles:
            continue

        plot_profile(
            config, figures_dir, repo,
            samples_df[samples_df["name"] == sample.name], experiments_df,
            cycles=cycles, modes=["discharge"], plot_cfg=rate_cfg.profiles_plot,
            save=rate_cfg.profiles_plot.save or "{sample}_profiles.svg", batch=batch_name)
        any_drawn = True

    if not any_drawn:
        print("⚠️  No rate profile cycles to plot.")
    else:
        print(f"✅  Figures written to {figures_dir}")


def run_plot(config, rate_cfg, repo, project_id: int, batch_name=None) -> None:
    plot_capacity(config, rate_cfg, repo, project_id, batch_name=batch_name)
    plot_ce(config, rate_cfg, repo, project_id, batch_name=batch_name)
    print(f"✅  Figures written to {_figures_dir(config, batch_name)}")
