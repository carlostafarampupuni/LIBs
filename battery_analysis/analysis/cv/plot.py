"""CV plots, reading straight from the database, styled from cv.yaml.

  by composition  one figure per cell (sheet), every scan rate overlaid —
                  CV_<sample>.svg (CV_<sheet>.svg when a sample has several
                  cells). Cells sharing a scan_rate_palette (e.g. a reference
                  cell and a doped cell) get the SAME colour per scan rate,
                  for direct visual comparison. Styled by `plot:`.
  by scan rate    one figure per scan rate, every sample overlaid in its
                  config.yaml colour — CV_<rate>.svg. A sample with several
                  cells is drawn from the FIRST of its cells listed under
                  `cells:` (reorder or comment cells out to choose). Styled
                  by `scan_rate_plot:`.

`samples:` (one per line) limits both to the samples listed, and
`active_mass_g:` turns current into current density (A/g) per sample.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    batch_filename,
    batch_path,
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, SAVE_DPI, figures_dir, nice_limits,
)

apply_style()

# Fallback palette (dark -> light) if cv.yaml doesn't set scan_rate_palette.
_DEFAULT_SCAN_COLORS = ["#4D4D4D", "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD", "#8C564B"]
_DEFAULT_YLABEL = "Current (A)"


def _figures_dir(config, batch_name=None) -> Path:
    return figures_dir(config, "CV", batch_name)


def _color_for_scan_rate(cv_cfg, scan_rate: float, idx: int) -> str:
    key = cv_cfg.scan_rate_palette.get(str(scan_rate)) or cv_cfg.scan_rate_palette.get(f"{scan_rate:g}")
    if key:
        return key
    return _DEFAULT_SCAN_COLORS[idx % len(_DEFAULT_SCAN_COLORS)]


def _rate_label(cv_cfg, scan_rate: float) -> str:
    return f"{scan_rate:g} {cv_cfg.scan_rate_unit}".strip()


def _sample_rank(listed, sample):
    """Legend order: the order of the YAML samples: list, else by the leading number (0, 2.5, 5, 10)."""
    if sample in listed:
        return (0, listed.index(sample), 0.0, sample)
    head = sample.split("%")[0]
    try:
        return (1, 0, float(head), sample)
    except ValueError:
        return (1, 0, float("inf"), sample)


def _shown_cells(cv_cfg):
    return [c for c in cv_cfg.cells if not cv_cfg.samples or c.sample in cv_cfg.samples]


def _ylabel(cv_cfg, plot_cfg) -> str:
    """Current density when masses are given and the YAML didn't set its own label."""
    if cv_cfg.active_mass_g and plot_cfg.ylabel == _DEFAULT_YLABEL:
        return "Current density (A/g)"
    return plot_cfg.ylabel


def _current(cv_cfg, sample, current):
    mass = cv_cfg.active_mass_g.get(sample)
    return current / mass if mass else current


def _shared_ylims(groups, curves_by_key):
    """{key: (ylim, ytick_step)} — one nice y-range per group of figure keys."""
    out = {}
    for keys in groups:
        ys = [y for k in keys for _, _, y in curves_by_key.get(k, [])]
        if not ys:
            continue
        lo, hi, step = nice_limits(min(float(np.min(y)) for y in ys), max(float(np.max(y)) for y in ys))
        out.update({k: ([lo, hi], step) for k in keys})
    return out


def _finish(ax, plot_cfg, ylabel, shared=None):
    ax.set_xlabel(plot_cfg.xlabel)
    ax.set_ylabel(ylabel)
    ylim, ystep = plot_cfg.ylim, plot_cfg.ytick_step
    if ylim is None and shared is not None:          # an explicit ylim always wins
        ylim, auto_step = shared
        ystep = ystep or auto_step
    format_axes(ax, plot_cfg.xlim, ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=ystep,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n, compact=plot_cfg.compact_ticks)
    leg = ax.legend(loc=plot_cfg.legend_loc, **legend_style)
    for t in leg.get_texts():
        t.set_color("black")


def plot_by_composition(config, cv_cfg, repo, project_id: int, exps, batch_name=None) -> int:
    """One figure per configured cell, every scan rate overlaid."""
    cells = _shown_cells(cv_cfg)
    sample_counts = {}
    for c in cells:
        sample_counts[c.sample] = sample_counts.get(c.sample, 0) + 1

    curves = {}                                      # sheet -> [(label, V, I)]
    for cell_cfg in cells:
        rows = exps[(exps["sample"] == cell_cfg.sample) & (exps["source_folder"] == cell_cfg.sheet)]
        if rows.empty:
            print(f"⚠️  {cell_cfg.sample!r} ({cell_cfg.sheet!r}) not found — skipping")
            continue
        experiment_id = int(rows.iloc[0]["experiment_id"])
        for i, scan_rate in enumerate(cell_cfg.scan_rates):
            df = repo.load_cv_points(experiment_id, scan_rate)
            if not df.empty:
                curves.setdefault(cell_cfg.sheet, []).append(
                    ((i, scan_rate), df["voltage"].to_numpy(), _current(cv_cfg, cell_cfg.sample, df["current"]).to_numpy()))
    shared = _shared_ylims([list(curves)], curves) if cv_cfg.plot.share_ylim else {}

    n = 0
    for cell_cfg in cells:
        if cell_cfg.sheet not in curves:
            continue
        fig, ax = plt.subplots(figsize=fig_sizes[cv_cfg.plot.figsize])
        for (i, scan_rate), v, y in curves[cell_cfg.sheet]:
            ax.plot(v, y, linewidth=1, color=_color_for_scan_rate(cv_cfg, scan_rate, i),
                    label=_rate_label(cv_cfg, scan_rate))
        _finish(ax, cv_cfg.plot, _ylabel(cv_cfg, cv_cfg.plot), shared.get(cell_cfg.sheet))
        fig.tight_layout()

        # Always per-cell — cv_cfg.plot.save is a single batch-wide
        # default and would collide across cells if reused here directly.
        # CV_<sample>.svg, or CV_<sheet>.svg when several cells (c1, c2)
        # of the same sample are in this batch.
        name = cell_cfg.sample if sample_counts[cell_cfg.sample] == 1 else cell_cfg.sheet
        save_name = f"CV_{name.replace(' ', '_')}.svg"
        fig.savefig(batch_path(_figures_dir(config, batch_name), save_name, batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
        finish_figure(fig)
        n += 1
    return n


def plot_by_scan_rate(config, cv_cfg, repo, project_id: int, exps, batch_name=None) -> int:
    """One figure per scan rate, every shown sample overlaid (its first listed cell)."""
    first_cell = {}
    for c in _shown_cells(cv_cfg):
        first_cell.setdefault(c.sample, c)
    first_cell = dict(sorted(first_cell.items(), key=lambda kv: _sample_rank(cv_cfg.samples, kv[0])))
    colors = dict(zip(*repo.list_samples(project_id)[["name", "color"]].values.T))
    rates = sorted({r for c in first_cell.values() for r in c.scan_rates})
    plot_cfg = cv_cfg.scan_rate_plot

    curves = {}                                      # scan rate -> [(sample, V, I)]
    for scan_rate in rates:
        for sample, cell_cfg in first_cell.items():
            rows = exps[(exps["sample"] == sample) & (exps["source_folder"] == cell_cfg.sheet)]
            if rows.empty or scan_rate not in cell_cfg.scan_rates:
                continue
            df = repo.load_cv_points(int(rows.iloc[0]["experiment_id"]), scan_rate)
            if not df.empty:
                curves.setdefault(scan_rate, []).append(
                    (sample, df["voltage"].to_numpy(), _current(cv_cfg, sample, df["current"]).to_numpy()))
    share = plot_cfg.share_ylim
    groups = [list(curves)] if share is True else [[float(r) for r in g] for g in share] if share else []
    shared = _shared_ylims(groups, curves)

    n = 0
    for scan_rate in rates:
        if scan_rate not in curves:
            continue
        fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
        for sample, v, y in curves[scan_rate]:
            ax.plot(v, y, linewidth=1, color=colors.get(sample), label=sample)
        _finish(ax, plot_cfg, _ylabel(cv_cfg, plot_cfg), shared.get(scan_rate))
        fig.tight_layout()
        save_name = f"CV_{scan_rate:g}{cv_cfg.scan_rate_unit.replace('/', '')}.svg"
        fig.savefig(batch_path(_figures_dir(config, batch_name), save_name, batch_name), dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
        finish_figure(fig)
        n += 1
    return n


def run_plot(config, cv_cfg, repo, project_id: int, batch_name=None, kind: str = "all") -> None:
    """The notebook-facing entry point (project.plot_cv()). kind: "all" | "composition" | "scan_rate"."""
    exps = repo.list_cv_experiments(project_id, batch_name=batch_name)
    if exps.empty:
        print(f"⚠️  No CV data imported for batch {batch_name!r} — nothing to plot.")
        return
    n = 0
    if kind in ("all", "composition"):
        n += plot_by_composition(config, cv_cfg, repo, project_id, exps, batch_name)
    if kind in ("all", "scan_rate"):
        n += plot_by_scan_rate(config, cv_cfg, repo, project_id, exps, batch_name)
    print(f"✅  {n} CV figure(s) written to {_figures_dir(config, batch_name)}")
