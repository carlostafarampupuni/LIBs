"""
Reads GCD cycle results back from the database and produces the narrative
report, plus the numbers behind it as tables (value_tables) — every cell,
every cycle, saved to exports/GCD_<batch>_cycles.csv and _summary.csv.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .analyzer import narrative as _narrative
from .discovery import current_density_from_folder

# What each value is — printed above the tables, so the numbers can be read
# without opening the code. Voltages are vs the counter electrode (Na/Na+ or
# Li/Li+); plating is the negative half-cycle.
GLOSSARY = """\
  V_nucleation      most negative voltage at the start of plating (the nucleation dip)
  V_growth          plating plateau = mean of the last plateau_frac of plating (steady growth)
  ΔV_nuc-growth     |V_nucleation − V_growth| = nucleation overpotential: the extra push to form nuclei
  dip genuine       the voltage recovers after the dip (a real nucleation barrier, not noise)
  V_strip_peak      highest stripping voltage in the middle 20–80 % of stripping
  V_strip_plateau   stripping plateau: median of 20–80 % of stripping (strip_plateau: middle)
                    or mean of the last plateau_frac of stripping (strip_plateau: end)
  hysteresis        |V_growth − V_strip_plateau|: plating–stripping voltage gap
  CE                stripped / plated · LLI = plated − stripped (cumulative = running total)
  strip shape       flat | peaked (tips stripped first) | rising (passivation, or the cut-off tail)"""

_VALUE_COLUMNS = {           # database column -> (table column, scale)
    "q_plate": ("Q_plate (mAh/cm2)", 1), "q_strip": ("Q_strip (mAh/cm2)", 1),
    "ce_pct": ("CE (%)", 1), "lli_cumul": ("LLI_cumul (mAh/cm2)", 1),
    "v_nucleation": ("V_nucleation (mV)", 1000), "v_plateau_plate": ("V_growth (mV)", 1000),
    "eta_nuc": ("ΔV_nuc-growth (mV)", 1000), "genuine_nuc_dip": ("dip genuine", 1),
    "v_pol_max": ("V_strip_peak (mV)", 1000), "v_plateau_strip": ("V_strip_plateau (mV)", 1000),
    "hysteresis": ("hysteresis (mV)", 1000), "strip_shape": ("strip shape", 1),
}


def selected_experiments(gcd_cfg, repo, project_id, batch_name):
    """
    Cells listed in gcd.yaml (in that order); without a cells: list, every
    cell of the batch. A cell imported again under a new sample name (after
    an edit to config.yaml) keeps only its newest import.
    """
    df = repo.list_gcd_experiments(project_id, batch_name=batch_name)
    df = df.sort_values("experiment_id").drop_duplicates("source_folder", keep="last")
    if gcd_cfg.cells:
        stems = df["source_folder"].map(lambda s: Path(s).stem)
        df = df[stems.isin(gcd_cfg.cells)].copy()
        df["_order"] = stems[df.index].map(gcd_cfg.cells.index)
        df = df.sort_values("_order")
    return df


def value_tables(config, gcd_cfg, repo, project_id: int, batch_name: str | None = None) -> dict:
    """
    {"cycles": one row per cell per cycle, "summary": one row per sample} —
    the summary gives cycle 1 and the all-cycle average, mean ± sd over the
    sample's cells. Both are also written to exports/GCD_<batch>_*.csv.
    """
    rows = []
    for _, e in selected_experiments(gcd_cfg, repo, project_id, batch_name).iterrows():
        df = repo.load_gcd_cycle_results(int(e["experiment_id"]))
        if df.empty:
            continue
        t = pd.DataFrame({"sample": e["sample"], "cell": Path(e["source_folder"]).stem, "cycle": df["cycle"]})
        for col, (name, scale) in _VALUE_COLUMNS.items():
            v = df[col]
            t[name] = v.astype(bool) if col == "genuine_nuc_dip" else (v * scale if scale != 1 else v)
        rows.append(t)
    if not rows:
        return {"cycles": pd.DataFrame(), "summary": pd.DataFrame()}
    cycles = pd.concat(rows, ignore_index=True)

    numeric = [n for c, (n, _) in _VALUE_COLUMNS.items() if c not in ("genuine_nuc_dip", "strip_shape")]
    first = cycles[cycles["cycle"] == cycles.groupby("cell")["cycle"].transform("min")]
    per_cell_mean = cycles.groupby(["sample", "cell"], sort=False)[numeric].mean().reset_index()
    parts = []
    for label, frame in (("cycle 1", first), ("all-cycle mean", per_cell_mean)):
        g = frame.groupby("sample", sort=False)[numeric]
        m, sd, n = g.mean(), g.std(), g.size()
        cell = m.round(2).astype(str) + " ± " + sd.round(2).fillna(0).astype(str)
        cell.insert(0, "n cells", n)
        cell.insert(0, "which", label)
        parts.append(cell.reset_index())
    summary = pd.concat(parts, ignore_index=True)

    out_dir = config.resolve(config.paths.exports_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = batch_name or "batch"
    cycles.to_csv(out_dir / f"GCD_{tag}_cycles.csv", index=False)
    summary.to_csv(out_dir / f"GCD_{tag}_summary.csv", index=False)
    return {"cycles": cycles, "summary": summary, "folder": out_dir}


def _sampling_s(repo, experiments_df) -> float:
    """Median time between recorded points, in seconds (first cell)."""
    if experiments_df.empty:
        return float("nan")
    trace = repo.load_gcd_trace(int(experiments_df.iloc[0]["experiment_id"]))
    return float(trace["time_h"].diff().median() * 3600) if len(trace) > 1 else float("nan")


def _stat(cycles, sample, col, cycle=None):
    """(mean, sd, n) over the sample's cells — at one cycle, or of each cell's all-cycle mean."""
    d = cycles[cycles["sample"] == sample]
    v = d[d["cycle"] == cycle][col] if cycle is not None else d.groupby("cell")[col].mean()
    v = v.dropna()
    return float(v.mean()), float(v.std()) if len(v) > 1 else float("nan"), len(v)


def _fmt(m, sd, n, unit, nd=1):
    return f"{m:.{nd}f} ± {sd:.{nd}f} {unit} (n={n})" if n > 1 else f"{m:.{nd}f} {unit} (n=1)"


def _versus(ref, other, a, b, unit, nd=1):
    """One sentence comparing sample `other` (b) to `ref` (a); a, b = (mean, sd, n)."""
    diff = b[0] - a[0]
    spread = max(x for x in (a[1], b[1], 0.0) if x == x)
    if a[2] < 2 or b[2] < 2:
        verdict = "only one cell on one side — repeat before reading anything into it"
    elif abs(diff) > 2 * spread:
        verdict = f"larger than the cell-to-cell scatter (sd ≤ {spread:.{nd}f}) → a real difference"
    else:
        verdict = f"within the cell-to-cell scatter (sd ≤ {spread:.{nd}f}) → not distinguishable yet"
    ratio = f", {b[0] / a[0]:.1f}×" if abs(a[0]) > 1e-9 and (b[0] / a[0]) > 0 else ""
    return f"{other} vs {ref}: {diff:+.{nd}f} {unit}{ratio} — {verdict}."


def findings(cycles: pd.DataFrame, sampling_s: float, batch_name=None) -> str:
    """
    What the numbers say, compared sample against sample — the first sample
    listed in gcd.yaml `cells:` is the reference. Each block names the
    figure it belongs to; the figures are the ones used in the literature
    for metal plating/stripping on a current collector.
    """
    samples = list(dict.fromkeys(cycles["sample"]))
    ref = samples[0]
    first = int(cycles["cycle"].min())
    last = int(cycles["cycle"].max())
    nuc, grow = "ΔV_nuc-growth (mV)", "V_growth (mV)"
    hyst, ce = "hysteresis (mV)", "CE (%)"
    L = [f"FINDINGS — {batch_name or 'batch'} ({len(samples)} sample(s), cycles {first}–{last}; "
         f"reference = {ref}; values are mean ± sd over cells)"]

    L.append("\n1. Nucleation — cycle-1 plating dip  [GCD_nucleation; the figure type of Yan et al., "
             "Nat. Energy 2016]")
    L.append("   ΔV_nuc-growth is the extra voltage needed to form the first nuclei on the bare surface;"
             " a smaller value means the surface takes the metal up more easily.")
    for s in samples:
        L.append(f"   {s:<18} ΔV = {_fmt(*_stat(cycles, s, nuc, first), 'mV')}, "
                 f"V_nucleation = {_stat(cycles, s, 'V_nucleation (mV)', first)[0]:.1f} mV, "
                 f"V_growth = {_stat(cycles, s, grow, first)[0]:.1f} mV")
    for s in samples[1:]:
        L.append("   " + _versus(ref, s, _stat(cycles, ref, nuc, first), _stat(cycles, s, nuc, first), "mV"))
    if last > first:
        for s in samples:
            a, b = _stat(cycles, s, nuc, first)[0], _stat(cycles, s, nuc, first + 1)[0]
            if a and b == b and b < 0.6 * a:
                L.append(f"   {s}: ΔV falls from {a:.1f} mV (cycle {first}) to {b:.1f} mV (cycle {first + 1}) — "
                         "after cycle 1 the metal plates onto what is left of the first deposit, so only "
                         "cycle 1 measures the bare surface.")
    if sampling_s == sampling_s and sampling_s > 5:
        L.append(f"   ⚠ points every {sampling_s:.0f} s: the true minimum can fall between two points, so "
                 "V_nucleation and ΔV are lower bounds. Record every 1 s during plating to resolve the dip.")

    L.append(f"\n2. Plating/stripping voltage — voltage–capacity profiles  [GCD_voltage-capacity]")
    for s in samples:
        L.append(f"   {s:<18} V_growth = {_fmt(*_stat(cycles, s, grow), 'mV')}, "
                 f"hysteresis = {_fmt(*_stat(cycles, s, hyst), 'mV')} (all-cycle mean)")
    for s in samples[1:]:
        L.append("   hysteresis " + _versus(ref, s, _stat(cycles, ref, hyst), _stat(cycles, s, hyst), "mV"))

    L.append(f"\n3. Reversibility — coulombic efficiency vs cycle  [GCD_CE]")
    L.append("   Cycle 1 includes building the first SEI on the bare foil, so it is reported apart from "
             "the later cycles.")
    later = cycles[cycles["cycle"] > first]
    for s in samples:
        line = f"   {s:<18} CE cycle {first} = {_fmt(*_stat(cycles, s, ce, first), '%', 2)}"
        if not later.empty:
            line += f", mean CE cycles {first + 1}–{last} = {_fmt(*_stat(later, s, ce), '%', 2)}"
        L.append(line)
    for s in samples[1:]:
        L.append("   CE cycle 1 " + _versus(ref, s, _stat(cycles, ref, ce, first), _stat(cycles, s, ce, first), "%", 2))
    for _, r in cycles[cycles[ce] > 100.5].iterrows():
        L.append(f"   ⚠ {r['cell']} cycle {int(r['cycle'])}: CE = {r[ce]:.1f} % (> 100 %) — more stripped "
                 "than plated: a soft short or metal left from an earlier cycle; look at that cycle in "
                 "GCD_voltage-time.")
    if last - first + 1 < 10:
        L.append(f"   ⚠ only {last - first + 1} cycle(s): too few for a CE trend — papers compare the "
                 "average CE over tens to hundreds of cycles.")

    small = [s for s in samples if cycles[cycles["sample"] == s]["cell"].nunique() < 3]
    if small:
        L.append(f"\n   Fewer than 3 cells: {', '.join(small)} — a difference is convincing once it holds "
                 "in ≥ 3 cells.")
    return "\n".join(L)


def run_analysis(config, gcd_cfg, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    """
    For every cell listed in gcd.yaml (commented-out cells are left out),
    load its cycle results and anomalies and produce a narrative. Returns a
    dict keyed by "sample — cell": {"narrative": str, "mean_ce": float, "n_cycles": int}.
    """
    experiments_df = selected_experiments(gcd_cfg, repo, project_id, batch_name)
    current_density = gcd_cfg.current_density_mA_cm2 or current_density_from_folder(
        batch_name or gcd_cfg.data_subfolder
    )

    out = {}
    for _, row in experiments_df.iterrows():
        df = repo.load_gcd_cycle_results(row["experiment_id"])
        if df.empty:
            continue
        anomalies_df = repo.load_gcd_anomalies(row["experiment_id"])
        anomalies = list(anomalies_df.itertuples(index=False, name=None))

        df_renamed = df.rename(columns={
            "q_plate": "Q_plate (mAh/cm2)", "q_strip": "Q_strip (mAh/cm2)", "ce_pct": "CE (%)",
            "lli": "LLI (mAh/cm2)", "lli_cumul": "LLI_cumul (mAh/cm2)", "cap_ret_pct": "cap_ret (%)",
            "t_nucleation_h": "t_nucleation (h)", "v_nucleation": "V_nucleation (V)",
            "genuine_nuc_dip": "genuine_nuc_dip", "v_plateau_plate": "V_plateau_plate (V)",
            "eta_nuc": "eta_nuc (V)", "t_pol_max_h": "t_pol_max (h)", "v_pol_max": "V_pol_max (V)",
            "v_plateau_strip": "V_plateau_strip (V)", "strip_shape": "strip_shape",
            "hysteresis": "hysteresis (V)",
        })

        label = f"{row['sample']} — {Path(row['source_folder']).stem}"   # one story per listed cell
        text = _narrative(df_renamed, anomalies, label, current_density)
        if verbose:
            print(text)

        out[label] = {
            "narrative": text,
            "mean_ce": float(df["ce_pct"].mean()),
            "n_cycles": len(df),
            "n_anomalies": len(anomalies_df),
        }

    tables = value_tables(config, gcd_cfg, repo, project_id, batch_name=batch_name)
    if verbose and not tables["cycles"].empty:
        print("\n" + findings(tables["cycles"], _sampling_s(repo, experiments_df), batch_name))
        with pd.option_context("display.width", 250, "display.max_columns", 30):
            print("\nWhat the values mean:\n" + GLOSSARY)
            print("\nPer sample (mean ± sd over its cells):")
            print(tables["summary"].to_string(index=False))
            print("\nEvery cell, every cycle:")
            print(tables["cycles"].round(3).to_string(index=False))
        print(f"\n✅  Tables → {tables['folder']}/GCD_{batch_name}_cycles.csv, _summary.csv")
    return out
