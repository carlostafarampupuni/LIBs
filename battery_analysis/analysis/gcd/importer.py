"""
Raw Zive CSV / NEWARE files -> experiment + trace + cycle-result rows.

For each discovered sample file: reads the raw trace (io.read_zive_csv,
or io.read_neware_gcd for NEWARE .xlsx/.csv/.ndax — Na‖Al / Na‖Cu),
stores it, runs analyze_gcd (segmentation -> nucleation/stripping metrics
-> CE/LLI per cycle -> anomaly detection), and stores the results. Runs
ce_method3 too if config.method3.enabled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from battery_analysis.config.schema import ProjectConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for

from .discovery import discover_gcd_files, current_density_from_folder, resolve_gcd_folder
from .io import read_zive_csv, read_neware_gcd, is_zive_csv
from .analyzer import analyze_gcd
from .ce_method3 import ce_method3


def _experiment_date(config: ProjectConfig, path) -> str | None:
    if path.name in config.experiment_dates:
        return config.experiment_dates[path.name]
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    except OSError:
        return None


def _neware_capacities(path, df, current_density):
    """
    Replace the current-density × time capacities with the ones NEWARE
    measured (per cycle, mAh -> mAh/cm² with area = current / current
    density), and recompute CE, LLI and retention from them.
    """
    from battery_analysis.analysis.cycling_common.neware_io import NewareParser
    p = NewareParser(path, ce="charge/discharge")
    rec = p.records
    i_mA = rec.loc[rec["direction"] != "rest", "current_mA"].abs().median()
    area = i_mA / current_density
    meas = p.cycle_summary().set_index("Cycle")
    df = df.copy()
    for k, row in df.iterrows():
        c = int(row["cycle"])
        if c in meas.index:
            df.at[k, "Q_plate (mAh/cm2)"] = round(meas.at[c, "Q_Discharge"] / area, 5)
            df.at[k, "Q_strip (mAh/cm2)"] = round(meas.at[c, "Q_Charge"] / area, 5)
    q_p, q_s = df["Q_plate (mAh/cm2)"], df["Q_strip (mAh/cm2)"]
    df["CE (%)"] = (100 * q_s / q_p).round(2)
    df["LLI (mAh/cm2)"] = (q_p - q_s).round(5)
    df["LLI_cumul (mAh/cm2)"] = df["LLI (mAh/cm2)"].cumsum().round(5)
    df["cap_ret (%)"] = (100 * q_s / q_s.iloc[0]).round(2)
    return df, area


def _warn_neware_cycles(name, trace, df, set_mA):
    """
    Print the cycles whose CE can't be read as Na reversibility:
    CE > 100 % (more stripped than plated: a soft short or a side reaction),
    a half-cycle the cycler ran at a different current from the set one,
    or plating that went below -1 V (cell failing / losing contact).
    The numbers are kept as measured; this only says which ones to distrust.
    """
    tr = trace.assign(half=((trace["phase"] != trace["phase"].shift()).cumsum()))
    halves = tr.groupby("half").agg(phase=("phase", "first"), i=("Current(mA)", lambda s: s.abs().median()),
                                    vmin=("Voltage(V)", "min"))
    # half-cycles in order: plating, stripping, plating, ... -> cycle number
    halves = halves[halves["phase"].eq("plating").cumsum() > 0]
    halves["cycle"] = halves["phase"].eq("plating").cumsum()
    off_i = sorted(set(halves.loc[(halves["i"] - set_mA).abs() > 0.03 * set_mA, "cycle"]) & set(df["cycle"]))
    deep = sorted(set(halves.loc[(halves["phase"] == "plating") & (halves["vmin"] < -1.0), "cycle"]) & set(df["cycle"]))
    over = df.loc[df["CE (%)"] > 100.5, "cycle"].astype(int).tolist()
    for what, cyc in (("CE > 100 % (stripped more than plated — soft short?)", over),
                      (f"current not at the set {set_mA:.2f} mA", off_i),
                      ("plating below −1 V", deep)):
        if cyc:
            print(f"   ⚠ {name}: {what} in cycle(s) {', '.join(map(str, cyc))}")


def import_gcd(config: ProjectConfig, gcd_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Auto-discover every GCD raw file under data_root/gcd_cfg.data_subfolder,
    import it, run the full analysis, and return the number of trace points
    imported.
    """
    data_dir = config.resolve(config.paths.data_root) / gcd_cfg.data_subfolder
    detected_density, batch_name = resolve_gcd_folder(gcd_cfg.data_subfolder)

    current_density = gcd_cfg.current_density_mA_cm2 or detected_density
    if current_density is None:
        # Voltage-only batch (e.g. a rate test where the current changes): the
        # traces are imported and plotted, per-cycle capacities/CE are skipped.
        print(f"ℹ️  {gcd_cfg.data_subfolder}: no current density (folder name or "
              "current_density_mA_cm2) — importing voltage traces only, no per-cycle analysis.")

    names = {**config.sample_aliases(), **gcd_cfg.sample_names}   # config.yaml aliases, gcd.yaml may override
    discovered = discover_gcd_files(data_dir, gcd_cfg.cells, names)
    if not discovered:
        print(f"⚠️  No GCD files found under {data_dir}")
        return 0

    total = 0
    imported_ids = set()
    for color_idx, sample in enumerate(discovered):
        global_override = config.sample_override(sample.name)
        color = (
            (global_override.color if global_override and global_override.color else None)
            or color_for(color_idx, config.palette or None)
        )
        cell = config.cell_metadata_for(sample.name)
        sample_id = repo.get_or_create_sample(project_id, sample.name, color=color)

        for file in sample.files:
            is_neware = not (file.path.suffix.lower() == ".csv" and is_zive_csv(file.path))
            if is_neware:
                trace = read_neware_gcd(file.path, cutoff_v=gcd_cfg.cutoff_v, plating_sign=gcd_cfg.plating_sign)
            else:
                trace = read_zive_csv(file.path, cutoff_v=gcd_cfg.cutoff_v, rest_hours=gcd_cfg.rest_hours)
            if trace is None or trace.empty:
                print(f"⚠️  {file.path.name}: could not read trace, skipping")
                continue

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="gcd",
                source_folder=file.path.name, batch_name=batch_name,
                replicate_number=file.replicate_number,
                is_representative=(file.replicate_number == sample.default_representative.replicate_number),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_experiment_date(config, file.path),
                notes=cell.notes,
            )
            imported_ids.add(experiment_id)
            repo.clear_gcd(experiment_id)
            repo.save_raw_file(experiment_id, str(file.path))
            repo.save_gcd_trace(experiment_id, trace["Time(h)"].to_numpy(), trace["Voltage(V)"].to_numpy())

            if current_density is None:
                total += len(trace)
                continue

            df, anomalies = analyze_gcd(
                trace["Time(h)"], trace["Voltage(V)"], sample.name, current_density,
                plating_sign=gcd_cfg.plating_sign, nuc_frac=gcd_cfg.nuc_frac,
                plateau_frac=gcd_cfg.plateau_frac, spike_z_thresh=gcd_cfg.spike_z_thresh,
                strip_plateau=gcd_cfg.strip_plateau,
                min_halfcycle_points=gcd_cfg.min_halfcycle_points,
                phase=trace["phase"] if is_neware else None,
                cutoff_v=gcd_cfg.cutoff_v if is_neware else None,
            )
            if is_neware and not df.empty:
                df, area = _neware_capacities(file.path, df, current_density)
                print(f"   {file.path.name} -> {sample.name}: {len(df)} cycle(s), electrode {area:.3f} cm² "
                      f"(from current / {current_density:g} mA cm⁻²)")
                _warn_neware_cycles(file.path.name, trace, df, area * current_density)
            repo.save_gcd_cycle_results(experiment_id, df)
            repo.save_gcd_anomalies(experiment_id, anomalies)

            if gcd_cfg.method3.enabled:
                m3 = ce_method3(
                    trace["Time(h)"].to_numpy(), trace["Voltage(V)"].to_numpy(), current_density,
                    n_cycles=gcd_cfg.method3.n_cycles,
                    stabilisation_hours=gcd_cfg.method3.stabilisation_hours,
                    qt_hours=gcd_cfg.method3.qt_hours, cycle_hours=gcd_cfg.method3.cycle_hours,
                )
                repo.save_gcd_method3_result(experiment_id, m3)

            total += len(trace)

    # Rows this import didn't touch are old copies (a cell imported before a
    # sample rename in config.yaml, or a file since deleted) — drop them.
    stale = repo.list_gcd_experiments(project_id, batch_name=batch_name)
    stale = stale[~stale["experiment_id"].isin(imported_ids)]
    for experiment_id in stale["experiment_id"]:
        repo.delete_gcd_experiment(int(experiment_id))
    if len(stale):
        print(f"   removed {len(stale)} old GCD row(s): {', '.join(stale['sample'] + ' / ' + stale['source_folder'])}")

    if total == 0:
        print("⚠️  No GCD data imported.")
    else:
        print(f"✅  Imported {total} GCD trace point(s) into the database.")
    return total
