"""
GCD for SUPERCAPACITORS (gcd.yaml batches with `mode: supercap`) —
galvanostatic charge-discharge at several current densities, the way an
EDLC paper reports it. Li-Li plating/stripping batches (the default
`mode: plating`) never come here; they keep using importer/analyzer/plot.

Raw files: the *_Capacity.csv / *_Cycle.csv pair per cell that the NOVA
ingest writes into Data/GCD/<batch>/ (the Capacity file carries a
"Current [A/g]" column, so cycles can be grouped by current density).

  import_gcd   registers every cell as a "gcd" experiment and marks one per
               sample as representative (representative: / sample_overrides:,
               same rules as rate.yaml).
  analyze_gcd  per sample and current density, from the `gcd_cycle`-th cycle:
                 Cs (F/g)  = Q_discharge x 3.6 / dV, dV = discharge span after the IR drop
                 IR drop   = V(end of charge) - V(first discharge point)
                 CE (%)    = Q_discharge / Q_charge
               -> printed, returned, and saved as exports/GCD_<batch>.csv
  plot_gcd     by composition: one figure per sample, current densities overlaid
                 -> Figures/GCD/GCD_<sample>_<batch>.svg
               by current density: one figure per current, samples overlaid
                 -> Figures/GCD/GCD_<j>Ag_<batch>.svg
               charge, then discharge continuing from the end of charge.

`samples:` (one per line) picks which samples are drawn; `plot:` styles both
figure types (xlim = capacity, ylim = voltage).
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from battery_analysis.analysis.cycling_common.discovery import discover_cycling_files, representative_file
from battery_analysis.analysis.cycling_common.io import _find_column, extract_cycle_profile, CyclingFileFormatError
from battery_analysis.analysis.rate.importer import _choose_representative
from battery_analysis.analysis.rate.parser import load_series
from battery_analysis.discovery import color_for
from battery_analysis.plotting.style import (
    apply_style, batch_filename, batch_path, fig_sizes, legend_style, format_axes, finish_figure, SAVE_DPI, figures_dir,
)

apply_style()

_CURRENT_COLORS = ["#7B1E1E", "#E08A2E", "#7A8B2A", "#2B4FC1", "#6A3D9A", "#1B9E77"]


def _data_dir(config, gcd_cfg) -> Path:
    return config.resolve(config.paths.data_root) / gcd_cfg.data_subfolder


def _rank(listed, sample):
    """Order: the YAML samples: list, else by the leading number (0, 2.5, 5, 10)."""
    if sample in listed:
        return (0, listed.index(sample), 0.0, sample)
    try:
        return (1, 0, float(sample.split("%")[0]), sample)
    except ValueError:
        return (1, 0, float("inf"), sample)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def import_gcd(config, gcd_cfg, repo, project_id: int) -> int:
    data_dir = _data_dir(config, gcd_cfg)
    batch_name = data_dir.name
    discovered = discover_cycling_files(data_dir)
    if not discovered:
        print(f"⚠️  No *_Capacity.csv files under {data_dir} — run the NOVA ingest first")
        return 0
    n = 0
    for idx, sample in enumerate(discovered):
        o = config.sample_override(sample.name)
        color = (o.color if o and o.color else None) or color_for(idx, config.palette or None)
        sample_id = repo.get_or_create_sample(project_id, sample.name, color=color)
        cell = config.cell_metadata_for(sample.name)
        loaded = [(f, s) for f in sample.files
                  if (s := load_series(sample.name, f.capacity_path)) is not None]
        if not loaded:
            continue
        chosen = _choose_representative(sample.name, loaded, gcd_cfg)
        for f, _ in loaded:
            exp_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="gcd", source_folder=f.capacity_path.name,
                batch_name=batch_name, replicate_number=f.replicate_number, is_representative=(f is chosen),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type, electrolyte=cell.electrolyte,
                additives=cell.additives, temperature_c=cell.temperature_c, notes=cell.notes,
                experiment_date=datetime.fromtimestamp(f.capacity_path.stat().st_mtime, tz=timezone.utc).date().isoformat(),
            )
            repo.save_raw_file(exp_id, str(f.capacity_path))
            if f.profile_path is not None:
                repo.save_raw_file(exp_id, str(f.profile_path))
            n += 1
    print(f"✅  Registered {n} GCD cell(s) for batch {batch_name}.")
    return n


# ---------------------------------------------------------------------------
# reading the representative cell of each sample
# ---------------------------------------------------------------------------

def _cycles_by_current(capacity_path) -> "OrderedDict[float, list]":
    df = pd.read_csv(capacity_path)
    df.columns = [c.strip() for c in df.columns]
    cyc = df[_find_column(df, "Cycle", capacity_path)].astype(int)
    cur = df[_find_column(df, "Current [A/g]", capacity_path)].astype(float)
    out = OrderedDict()
    for c, j in zip(cyc, cur):
        out.setdefault(j, []).append(c)
    return out


def _load(config, gcd_cfg, repo, project_id, batch_name):
    """{sample: {current density: (q_ch, v_ch, q_dis, v_dis)}} of each shown sample's representative cell."""
    exps = repo.list_technique_experiments(project_id, "gcd", batch_name=batch_name)
    discovered = {s.name: s for s in discover_cycling_files(_data_dir(config, gcd_cfg))}
    out = {}
    for name, sample in discovered.items():
        if gcd_cfg.samples and name not in gcd_cfg.samples:
            continue
        f = representative_file(sample, exps)
        if f is None or f.profile_path is None:
            continue
        try:
            groups = _cycles_by_current(f.capacity_path)
        except CyclingFileFormatError:
            print(f"⚠️  {f.capacity_path.name} has no 'Current [A/g]' column — re-run the NOVA ingest")
            continue
        curves = {}
        for j, cycles in groups.items():
            cycle = cycles[gcd_cfg.gcd_cycle - 1] if len(cycles) >= gcd_cfg.gcd_cycle else cycles[-1]
            v_ch, q_ch = extract_cycle_profile(f.profile_path, cycle, "charge")
            v_dis, q_dis = extract_cycle_profile(f.profile_path, cycle, "discharge")
            if v_ch is not None and v_dis is not None:
                curves[j] = (q_ch, v_ch, q_dis, v_dis)
        out[name] = _drop_impossible(f"{name}{f.replicate_number}", curves)
    return out


def _cs(curve) -> float:
    q_ch, v_ch, q_dis, v_dis = curve
    span = float(v_dis[0] - v_dis[-1])
    return q_dis[-1] * 3.6 / span if span > 0 else np.nan


def _drop_impossible(cell: str, curves: dict, tolerance: float = 1.15) -> dict:
    """A capacitor cannot store MORE at a higher current. A step whose Cs exceeds the Cs at a
    lower current density is a mislabelled or broken run (e.g. a 0.5 A/g run saved under
    "CD 2A" reads as 4x the capacitance) — left out, with a warning."""
    kept, lowest = {}, None
    for j in sorted(curves):
        cs = _cs(curves[j])
        if lowest is not None and cs > tolerance * lowest:
            print(f"⚠️  {cell}: {j:g} A/g gives {cs:.1f} F/g, above the {lowest:.1f} F/g at a lower "
                  f"current — left out (wrong current label or broken run)")
            continue
        kept[j] = curves[j]
        lowest = cs if lowest is None else min(lowest, cs)
    return kept


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

def run_analysis(config, gcd_cfg, repo, project_id: int, verbose: bool = True, batch_name=None) -> dict:
    data = _load(config, gcd_cfg, repo, project_id, batch_name)
    rows = []
    for sample in sorted(data, key=lambda s: _rank(gcd_cfg.samples, s)):
        for j, (q_ch, v_ch, q_dis, v_dis) in sorted(data[sample].items()):
            rows.append({"sample": sample, "current_A_g": j,
                         "Cs_F_g": _cs((q_ch, v_ch, q_dis, v_dis)),
                         "IR_drop_V": float(v_ch[-1] - v_dis[0]),
                         "CE_pct": 100 * q_dis[-1] / q_ch[-1] if q_ch[-1] else np.nan,
                         "Q_discharge_mAh_g": float(q_dis[-1])})
    table = pd.DataFrame(rows)
    if table.empty:
        print(f"⚠️  No GCD data to analyse for batch {batch_name!r}.")
        return {"table": table}
    out_dir = config.resolve(config.paths.exports_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / f"GCD_{batch_name}.csv", index=False)
    if verbose:
        wide = table.pivot(index="current_A_g", columns="sample", values="Cs_F_g")
        wide = wide[[s for s in sorted(wide.columns, key=lambda s: _rank(gcd_cfg.samples, s))]]
        print(f"GCD specific capacitance (F/g), cycle {gcd_cfg.gcd_cycle} at each current density:")
        print(wide.round(1).to_string())
        lo, hi = wide.index.min(), wide.index.max()
        print(f"Rate retention {hi:g} vs {lo:g} A/g (%): " +
              ", ".join(f"{s} {100 * wide.loc[hi, s] / wide.loc[lo, s]:.0f}" for s in wide.columns))
        print(f"✅  Table written to {out_dir / f'GCD_{batch_name}.csv'}")
    return {"table": table}


# ---------------------------------------------------------------------------
# plot
# ---------------------------------------------------------------------------

def _finish(fig, ax, plot_cfg, save, config, batch_name):
    ax.set_xlabel("Specific capacity (mAh/g)")
    ax.set_ylabel("Voltage (V)")
    format_axes(ax, plot_cfg.xlim, plot_cfg.ylim,
                xtick_step=plot_cfg.xtick_step, ytick_step=plot_cfg.ytick_step,
                xticks_n=plot_cfg.xticks_n, yticks_n=plot_cfg.yticks_n, compact=plot_cfg.compact_ticks)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc=plot_cfg.legend_loc, **legend_style)
    fig.tight_layout()
    fig.savefig(batch_path(figures_dir(config, "GCD"), save, batch_name),
                dpi=SAVE_DPI, bbox_inches="tight", transparent=True)
    finish_figure(fig)


def _curve(c):
    q_ch, v_ch, q_dis, v_dis = c
    return np.concatenate([q_ch, q_ch[-1] + q_dis]), np.concatenate([v_ch, v_dis])


def run_plot(config, gcd_cfg, repo, project_id: int, batch_name=None, kind: str = "all") -> None:
    """kind: "all" | "composition" | "current"."""
    data = _load(config, gcd_cfg, repo, project_id, batch_name)
    if not data:
        print(f"⚠️  No GCD profiles to plot for batch {batch_name!r} — run import_gcd() first.")
        return
    colors = dict(zip(*repo.list_samples(project_id)[["name", "color"]].values.T))
    order = sorted(data, key=lambda s: _rank(gcd_cfg.samples, s))
    currents = sorted({j for curves in data.values() for j in curves})
    plot_cfg = gcd_cfg.plot
    n = 0
    if kind in ("all", "composition"):
        for sample in order:
            fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
            for k, j in enumerate(currents):
                if j in data[sample]:
                    color = gcd_cfg.current_palette.get(f"{j:g}") or _CURRENT_COLORS[k % len(_CURRENT_COLORS)]
                    ax.plot(*_curve(data[sample][j]), linewidth=1, color=color, label=f"{j:g} A/g")
            _finish(fig, ax, plot_cfg, f"GCD_{sample}.svg", config, batch_name)
            n += 1
    if kind in ("all", "current"):
        for j in currents:
            fig, ax = plt.subplots(figsize=fig_sizes[plot_cfg.figsize])
            for sample in order:
                if j in data[sample]:
                    ax.plot(*_curve(data[sample][j]), linewidth=1, color=colors.get(sample), label=sample)
            _finish(fig, ax, plot_cfg, f"GCD_{j:g}Ag.svg", config, batch_name)
            n += 1
    print(f"✅  {n} GCD figure(s) written to {figures_dir(config, 'GCD')}")
