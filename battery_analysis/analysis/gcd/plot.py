"""
GCD plotting — everything comes out of the database that import_gcd() filled.

Which cells are drawn: exactly the cells listed in gcd.yaml `cells:` —
comment a line out to drop one; that is the only switch. (No `cells:`
list at all = every file in the folder.)
Sample names and colours come from config.yaml (`samples:`), so a sample
looks the same in every technique.

Every figure has its own block in gcd.yaml — `<name>_plot` draws all
samples on one axis (`_all`), `<name>_separate_plot` draws one figure per
sample (`_<sample>`, its cells told apart by shade). Each block has its own
figsize, limits and legend; comment a block out and that figure is not drawn.

    GCD_voltage-time      voltage vs time, whole test     voltage_plot      voltage_separate_plot
    GCD_voltage-capacity  voltage vs areal capacity       profile_plot      profile_separate_plot
    GCD_CE                coulombic efficiency vs cycle   ce_plot           ce_separate_plot
    GCD_nucleation        cycle-1 plating dip             nucleation_plot   nucleation_separate_plot

Extra per-cycle figures (all samples): capacity_plot, lli_plot,
overpotential_plot, hysteresis_plot.

Files: Figures/GCD/<name>_<all|sample>_<batch>.svg
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from battery_analysis.plotting.style import (
    apply_style, fig_sizes, legend_style, format_axes, finish_figure, SAVE_DPI, figures_dir,
    batch_filename,
    batch_path, SCATTER, LINE, PROFILE_LINE, CYCLE_PROFILE_LINE, ALPHA_MIN, ALPHA_MAX,
    chem_label, ordinal,
)
from .report import selected_experiments

apply_style()

CAPACITY_LABEL = "Capacity (mAh cm$^{-2}$)"


def _figures_dir(config) -> Path:
    return figures_dir(config, "GCD")


def _samples(config, gcd_cfg, repo, project_id, batch_name):
    """[(sample name, colour, its experiment rows)] in the order of gcd.yaml `cells:`."""
    experiments_df = selected_experiments(gcd_cfg, repo, project_id, batch_name)
    db_colours = dict(zip(*repo.list_samples(project_id)[["name", "color"]].T.values))
    out = []
    for name in dict.fromkeys(experiments_df["sample"]):
        override = config.sample_override(name)       # config.yaml colour wins, so edits apply at once
        colour = (override.color if override and override.color else None) or db_colours.get(name, "0.3")
        out.append((name, colour, experiments_df[experiments_df["sample"] == name]))
    return out


def _cell_alphas(n):
    """Separate figures: first cell full colour, later cells lighter."""
    return np.linspace(ALPHA_MAX, ALPHA_MIN, n) if n > 1 else [ALPHA_MAX]


def _cell_label(row):
    """The cell's name without its replicate number: SampleB-1 -> SampleB."""
    return re.sub(r"[-_ ]?\d+$", "", Path(row["source_folder"]).stem)


def _new_axes(plot_cfg, i=0):
    """i = which separate figure (cells order); a figsize list picks its size, the last one repeats."""
    sizes = plot_cfg.figsize if isinstance(plot_cfg.figsize, list) else [plot_cfg.figsize]
    fig, ax = plt.subplots(figsize=fig_sizes[sizes[min(i, len(sizes) - 1)]])
    fig.patch.set_alpha(0)
    return fig, ax


def _finish(fig, ax, plot_cfg, config, save, batch_name, xlabel, ylabel):
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        seen = {}
        for h, l in zip(handles, labels):          # one entry per sample, whichever cells are drawn
            seen.setdefault(l, h)
        ax.legend(list(seen.values()), [chem_label(l) for l in seen], loc=plot_cfg.legend_loc, **legend_style)
    fig.tight_layout()
    fig.savefig(batch_path(_figures_dir(config), save, batch_name), dpi=SAVE_DPI,
                bbox_inches="tight", transparent=True)
    finish_figure(fig)


def _safe(name) -> str:
    return str(name).replace(" ", "_").replace("%", "pct").replace("/", "-")


def _both(combined_cfg, separate_cfg, samples, draw, finish):
    """
    Run one figure in its two forms, each with its own yaml block (None =
    block commented out = not drawn). draw(ax, sample, colour, rows, separate)
    puts the lines on; finish(fig, ax, tag, cfg) labels and saves (no titles —
    the file name says which sample it is).
    """
    if combined_cfg is not None:
        fig, ax = _new_axes(combined_cfg)
        for name, colour, rows in samples:
            draw(ax, name, colour, rows, False)
        finish(fig, ax, "all", combined_cfg)
    if separate_cfg is not None:
        for i, (name, colour, rows) in enumerate(samples):
            fig, ax = _new_axes(separate_cfg, i)
            draw(ax, name, colour, rows, True)
            finish(fig, ax, _safe(name), separate_cfg)


# ---------------------------------------------------------------- raw curves

def _half_cycles(config, gcd_cfg, source_folder):
    """{cycle: ((q_plate, v_plate), (q_strip, v_strip))} re-read from the raw file, q in mAh/cm²."""
    from .discovery import resolve_gcd_folder
    path = config.resolve(config.paths.data_root) / gcd_cfg.data_subfolder / source_folder
    j = gcd_cfg.current_density_mA_cm2 or resolve_gcd_folder(gcd_cfg.data_subfolder)[0]
    if not j:
        # No current density: capacity per cm² can't be worked out, so the
        # voltage–capacity figure is skipped (the import already said why).
        return {}
    return _read_half_cycles(path, path.stat().st_mtime, j, gcd_cfg.cutoff_v, gcd_cfg.rest_hours,
                             gcd_cfg.plating_sign, gcd_cfg.min_halfcycle_points)


@lru_cache(maxsize=32)   # several figures need the same file; read it once (re-read if the file changes)
def _read_half_cycles(path, mtime, j, cutoff_v, rest_hours, plating_sign, min_points):
    from .analyzer import _pair_cycles, _segment_by_phase, _segment_halfcycles
    from .io import is_zive_csv, read_neware_gcd, read_zive_csv
    if path.suffix.lower() == ".csv" and is_zive_csv(path):
        tr = read_zive_csv(path, cutoff_v=cutoff_v, rest_hours=rest_hours)
        segs = _segment_halfcycles(tr["Time(h)"], tr["Voltage(V)"], plating_sign, min_points)
    else:
        tr = read_neware_gcd(path, cutoff_v=cutoff_v, plating_sign=plating_sign)
        # x = the capacity the cycler measured (mAh -> mAh/cm², area = set current / j),
        # not time × j: right even where the cycler ran a step at another current
        area = tr["Current(mA)"].abs().median() / j
        segs = _segment_by_phase(tr["Capacity(mAh)"] / area / j, tr["Voltage(V)"], tr["phase"], min_points)
    out = {}
    for k, ((px, py), (sx, sy)) in enumerate(_pair_cycles(segs), start=1):
        out[k] = ((j * (px - px.iloc[0]), py), (j * (sx - sx.iloc[0]), sy))
    return out


# ---------------------------------------------------------------- main figures

def plot_voltage_time(config, gcd_cfg, repo, project_id, batch_name=None):
    """Voltage vs time for the whole test."""
    samples = _samples(config, gcd_cfg, repo, project_id, batch_name)

    def draw(ax, name, colour, rows, separate):
        for (_, row), a in zip(rows.iterrows(), _cell_alphas(len(rows)) if separate else [1] * len(rows)):
            trace = repo.load_gcd_trace(int(row["experiment_id"]))
            if not trace.empty:
                ax.plot(trace["time_h"], trace["voltage_v"], color=colour, alpha=a, **PROFILE_LINE,
                        label=_cell_label(row) if separate else name)

    _both(gcd_cfg.voltage_plot, gcd_cfg.voltage_separate_plot, samples, draw, lambda fig, ax, tag, cfg: _finish(
        fig, ax, cfg, config, f"GCD_Time_{tag}.svg", batch_name, "Time (h)", "Voltage (V)"))


def plot_voltage_capacity(config, gcd_cfg, repo, project_id, batch_name=None):
    """
    Voltage vs areal capacity of every listed cell. Combined:
    profile_plot.combined_cycle. Separate: profile_cycles, later cycles lighter.
    """
    combined_cycle = gcd_cfg.profile_plot.combined_cycle if gcd_cfg.profile_plot else 1
    samples = _samples(config, gcd_cfg, repo, project_id, batch_name)

    def draw(ax, name, colour, rows, separate):
        cycles = gcd_cfg.profile_cycles if separate else [combined_cycle]
        alphas = np.linspace(ALPHA_MAX, ALPHA_MIN, len(cycles)) if separate and len(cycles) > 1 else [ALPHA_MAX]
        for _, row in rows.iterrows():
            halves = _half_cycles(config, gcd_cfg, row["source_folder"])
            for c, a in zip(cycles, alphas):
                if c not in halves:
                    continue
                (qp, vp), (qs, vs) = halves[c]
                label = ordinal(c) if separate else name
                ax.plot(qp, vp, color=colour, alpha=a, **CYCLE_PROFILE_LINE, label=label)
                ax.plot(qs, vs, color=colour, alpha=a, **CYCLE_PROFILE_LINE)

    def finish(fig, ax, tag, cfg):
        _finish(fig, ax, cfg, config, f"GCD_Capacity_{tag}.svg", batch_name,
                CAPACITY_LABEL, "Voltage (V)")

    _both(gcd_cfg.profile_plot, gcd_cfg.profile_separate_plot, samples, draw, finish)


def _per_cycle(config, gcd_cfg, repo, project_id, batch_name, column, plot_cfg, separate_cfg, save, ylabel,
               scale=1.0, ref_line=None):
    """Any per-cycle result (CE, capacity, LLI, overpotential, hysteresis) against cycle."""
    samples = _samples(config, gcd_cfg, repo, project_id, batch_name)

    def draw(ax, name, colour, rows, separate):
        for (_, row), a in zip(rows.iterrows(), _cell_alphas(len(rows)) if separate else [1] * len(rows)):
            df = repo.load_gcd_cycle_results(int(row["experiment_id"]))
            if df.empty:
                continue
            y = df[column] * scale
            ax.plot(df["cycle"], y, color=colour, alpha=a, **LINE)
            ax.scatter(df["cycle"], y, s=SCATTER["marker_size"], color=colour, alpha=a,
                       edgecolors=SCATTER["solid_edge_color"], linewidth=SCATTER["solid_edge_width"],
                       label=_cell_label(row) if separate else name, zorder=3)

    def finish(fig, ax, tag, cfg):
        if ref_line is not None:
            ax.axhline(ref_line, color="0.6", lw=0.6, ls="--", zorder=0)
        ax.xaxis.get_major_locator().set_params(integer=True)
        _finish(fig, ax, cfg, config, save.replace(".svg", f"_{tag}.svg"), batch_name,
                "Cycle No.", ylabel)

    _both(plot_cfg, separate_cfg, samples, draw, finish)


def plot_nucleation(config, gcd_cfg, repo, project_id, batch_name=None):
    """
    Cycle-1 plating, voltage (mV) vs capacity, zoomed on the start: the dip
    is nucleation, the flat part after it is growth. Separate figures also
    mark V_nucleation and V_growth of each listed cell and write its ΔV.
    """
    samples = _samples(config, gcd_cfg, repo, project_id, batch_name)

    def draw(ax, name, colour, rows, separate):
        alphas = _cell_alphas(len(rows)) if separate else [1] * len(rows)
        notes = []
        for (_, row), a in zip(rows.iterrows(), alphas):
            halves = _half_cycles(config, gcd_cfg, row["source_folder"])
            if 1 not in halves:
                continue
            (qp, vp), _ = halves[1]
            ax.plot(qp, vp * 1000, color=colour, alpha=a, **CYCLE_PROFILE_LINE,
                    label=_cell_label(row) if separate else name)
            res = repo.load_gcd_cycle_results(int(row["experiment_id"]))
            if separate and not res.empty:
                v_nuc, v_gr = res["v_nucleation"].iloc[0] * 1000, res["v_plateau_plate"].iloc[0] * 1000
                for v in (v_nuc, v_gr):
                    ax.axhline(v, color=colour, alpha=a, lw=0.5, ls=":", zorder=0)
                notes.append(f"ΔV = {abs(v_nuc - v_gr):.1f} mV")   # the legend already names the cell
        if notes:
            ax.text(0.97, 0.06, "\n".join(notes), transform=ax.transAxes, ha="right", va="bottom", fontsize=7)

    _both(gcd_cfg.nucleation_plot, gcd_cfg.nucleation_separate_plot, samples, draw,
          lambda fig, ax, tag, cfg: _finish(fig, ax, cfg, config, f"GCD_nucleation_{tag}.svg", batch_name,
                                            CAPACITY_LABEL, "Voltage (mV)"))


def run_plot(config, gcd_cfg, repo, project_id: int, batch_name=None) -> None:
    args = (config, gcd_cfg, repo, project_id, batch_name)
    plot_voltage_time(*args)
    plot_voltage_capacity(*args)
    _per_cycle(*args, "ce_pct", gcd_cfg.ce_plot, gcd_cfg.ce_separate_plot, "GCD_CE.svg", "CE (%)", ref_line=100)
    plot_nucleation(*args)
    extras = [  # (yaml block, column, file, y label, scale)
        (gcd_cfg.capacity_plot, "q_strip", "GCD_capacity.svg", "Stripped capacity (mAh cm$^{-2}$)", 1),
        (gcd_cfg.lli_plot, "lli_cumul", "GCD_LLI.svg", "Cumulative LLI (mAh cm$^{-2}$)", 1),
        (gcd_cfg.overpotential_plot, "eta_nuc", "GCD_overpotential.svg", "Nucleation overpotential (mV)", 1000),
        (gcd_cfg.hysteresis_plot, "hysteresis", "GCD_hysteresis.svg", "Hysteresis (mV)", 1000),
    ]
    for plot_cfg, column, save, ylabel, scale in extras:
        _per_cycle(*args, column, plot_cfg, None, save, ylabel, scale=scale)
    print(f"✅  Figures written to {_figures_dir(config)}")
