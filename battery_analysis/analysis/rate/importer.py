"""Raw *_Capacity.csv files (rate-capability test) -> experiment + rate_point rows."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from battery_analysis.config.schema import ProjectConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for
from battery_analysis.analysis.cycling_common.discovery import discover_cycling_files

from .parser import load_series


def _experiment_date(config: ProjectConfig, capacity_path) -> str | None:
    filename = capacity_path.name
    if filename in config.experiment_dates:
        return config.experiment_dates[filename]
    try:
        return datetime.fromtimestamp(
            capacity_path.stat().st_mtime, tz=timezone.utc
        ).date().isoformat()
    except OSError:
        return None


def _low_current_capacity(file, series) -> float:
    """Mean discharge capacity at the lowest current density (the "Current [A/g]" column the
    NOVA ingest writes), or of the first three cycles when the file has no current column."""
    import pandas as pd
    try:
        df = pd.read_csv(file.capacity_path)
        cur = df[[c for c in df.columns if c.strip().lower().startswith("current")][0]].to_numpy(float)
        n = min(len(cur), len(series.capacity_discharge))
        lowest = cur[:n] == np.nanmin(cur[:n])
        return float(np.nanmean(series.capacity_discharge[:n][lowest]))
    except (IndexError, KeyError, ValueError, OSError):
        return float(np.nanmean(series.capacity_discharge[:3]))


def _choose_representative(sample_name, loaded, rate_cfg):
    """Pick the representative (file, series) entry per rate.yaml representative: / sample_overrides."""
    files = [f for f, _ in loaded]
    override = rate_cfg.sample_override(sample_name)
    if override and override.plot:
        for f in files:
            if f"{sample_name}{f.replicate_number}" in override.plot or f.capacity_path.name in override.plot:
                return f
        print(f"⚠️  {sample_name}: sample_overrides plot {override.plot} matches no cell — using the default rule")
    rule = rate_cfg.representative
    if rule == "first" or len(loaded) == 1:
        return min(files, key=lambda f: f.replicate_number)
    if rule not in ("best", "median"):
        raise ValueError(f"rate.yaml representative: {rule!r} — use first, best or median")
    cap = {f.replicate_number: _low_current_capacity(f, s) for f, s in loaded}    # keyed by cell number
    ce = {f.replicate_number: float(np.nanmean(s.ce_pct)) for f, s in loaded}
    if rule == "best":
        ok = [f for f in files if ce[f.replicate_number] >= 75] or files
        pick = max(ok, key=lambda f: cap[f.replicate_number])
    else:
        med = float(np.median(list(cap.values())))
        pick = min(files, key=lambda f: abs(cap[f.replicate_number] - med))
    listing = ", ".join(f"{sample_name}{f.replicate_number} {cap[f.replicate_number]:.1f} mAh/g "
                        f"(CE {ce[f.replicate_number]:.0f} %)" for f in sorted(files, key=lambda f: f.replicate_number))
    print(f"🏅  {sample_name}: {listing}  ->  {sample_name}{pick.replicate_number} ({rule})")
    return pick


def import_rate(config: ProjectConfig, rate_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Auto-discover every *_Capacity.csv under data_root/rate_cfg.data_subfolder,
    import it (applying rate_cfg.electrode_type's charge/discharge convention),
    and return the number of rate points imported.

    A batch with any `espec_channels:` entries is imported entirely via
    the ESPEC path instead — see _import_rate_espec, and
    cycle/importer.py's _import_cycle_espec for the same pattern.
    """
    if rate_cfg.espec_channels:
        return _import_rate_espec(config, rate_cfg, repo, project_id)

    data_dir = config.resolve(config.paths.data_root) / rate_cfg.data_subfolder
    batch_name = data_dir.name

    discovered = discover_cycling_files(data_dir)
    if not discovered:
        print(f"⚠️  No *_Capacity.csv files found under {data_dir}")
        return 0

    total = 0
    for color_idx, sample in enumerate(discovered):
        global_override = config.sample_override(sample.name)
        color = (
            (global_override.color if global_override and global_override.color else None)
            or color_for(color_idx, config.palette or None)
        )
        cell = config.cell_metadata_for(sample.name)
        sample_id = repo.get_or_create_sample(project_id, sample.name, color=color)

        loaded = []
        for file in sample.files:
            series = load_series(sample.name, file.capacity_path, electrode_type=rate_cfg.electrode_type)
            if series is None:
                print(f"⚠️  {file.capacity_path.name}: no valid rows, skipping")
                continue
            loaded.append((file, series))
        if not loaded:
            continue
        chosen = _choose_representative(sample.name, loaded, rate_cfg)

        for file, series in loaded:
            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="rate",
                source_folder=file.capacity_path.name, batch_name=batch_name,
                replicate_number=file.replicate_number,
                is_representative=(file is chosen),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_experiment_date(config, file.capacity_path),
                notes=cell.notes,
            )
            repo.clear_rate_points(experiment_id)
            repo.save_raw_file(experiment_id, str(file.capacity_path))
            if file.profile_path is not None:
                repo.save_raw_file(experiment_id, str(file.profile_path))

            repo.save_rate_points(
                experiment_id, series.cycle_number, series.capacity_charge,
                series.capacity_discharge, series.ce_pct,
            )
            total += len(series.cycle_number)

    if total == 0:
        print("⚠️  No rate data imported.")
    else:
        print(f"✅  Imported {total} rate point(s) into the database.")
    return total


def _import_rate_espec(config: ProjectConfig, rate_cfg, repo: ProjectRepository, project_id: int) -> int:
    """ESPEC-format counterpart to import_rate() — see ESPECRateChannelConfig (config/schema.py)."""
    from battery_analysis.analysis.cycling_common.espec_io import (
        ESPECParser, find_espec_file, rate_series_from_espec,
    )

    data_dir = config.resolve(config.paths.data_root) / rate_cfg.data_subfolder
    batch_name = data_dir.name

    samples_order: list[str] = []
    by_sample: dict[str, list] = {}
    for ch in rate_cfg.espec_channels:
        by_sample.setdefault(ch.sample, []).append(ch)
        if ch.sample not in samples_order:
            samples_order.append(ch.sample)

    total = 0
    for color_idx, sample_name in enumerate(samples_order):
        channels = sorted(by_sample[sample_name], key=lambda c: c.replicate)
        default_replicate = min(c.replicate for c in channels)

        global_override = config.sample_override(sample_name)
        color = (
            (global_override.color if global_override and global_override.color else None)
            or color_for(color_idx, config.palette or None)
        )
        cell = config.cell_metadata_for(sample_name)
        sample_id = repo.get_or_create_sample(project_id, sample_name, color=color)

        for ch in channels:
            if ch.active_mass_g is None:
                print(
                    f"⚠️  {ch.channel} ({sample_name}): no active_mass_g set in rate.yaml — "
                    f"skipping (can't convert mAh -> mAh/g without it)"
                )
                continue
            raw_path = find_espec_file(data_dir, ch.channel)
            if raw_path is None:
                print(f"⚠️  {ch.channel}: no *_All.csv found under {data_dir}, skipping")
                continue

            try:
                parser = ESPECParser(
                    raw_path, active_mass_g=ch.active_mass_g,
                    formation_ptn=ch.formation_ptn, rate_ptns=ch.rate_ptns,
                )
            except (ValueError, OSError) as e:
                print(f"⚠️  {ch.channel}: could not parse {raw_path.name} ({e}), skipping")
                continue

            series = rate_series_from_espec(parser, sample_name, electrode_type=rate_cfg.electrode_type)
            if series is None or len(series.cycle_number) == 0:
                print(f"⚠️  {ch.channel}: no valid rate rows parsed, skipping")
                continue

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="rate",
                source_folder=raw_path.name, batch_name=batch_name,
                replicate_number=ch.replicate,
                is_representative=(ch.replicate == default_replicate),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_experiment_date(config, raw_path),
                notes=cell.notes,
            )
            repo.clear_rate_points(experiment_id)
            repo.save_raw_file(experiment_id, str(raw_path))

            repo.save_rate_points(
                experiment_id, series.cycle_number, series.capacity_charge,
                series.capacity_discharge, series.ce_pct,
            )
            total += len(series.cycle_number)

    if total == 0:
        print("⚠️  No rate data imported.")
    else:
        print(f"✅  Imported {total} rate point(s) into the database.")
    return total


def import_rate_profiles(config: ProjectConfig, rate_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Voltage-vs-capacity profile points for this batch's formation_cycles
    + custom_cycles + auto rate-capability sequence (see profiles: and
    rate_capability: in rate.yaml). Run import_rate() first: this keys
    its rows to the experiment import_rate() already created for each
    sample, it doesn't create its own.

    The rate-capability sequence is computed PER SAMPLE (each sample's
    *_Cycle.csv can end at a different cycle), so cycle_numbers is a
    callable here rather than one fixed list for every sample — see
    import_profile_points().

    ESPEC batches (espec_channels: set) take a separate path — see
    _import_rate_profiles_espec — since there's no *_Cycle.csv to read
    and no "ordinal cycle number" the way Wonatech has one; the same
    _All.csv already read by import_rate() has the voltage profile too.
    """
    if rate_cfg.espec_channels:
        return _import_rate_profiles_espec(config, rate_cfg, repo, project_id)

    from battery_analysis.analysis.cycling_common.profiles import import_profile_points, rate_capability_cycles

    data_dir = config.resolve(config.paths.data_root) / rate_cfg.data_subfolder
    batch_name = data_dir.name
    pick, cap = rate_cfg.profiles, rate_cfg.rate_capability
    formation_count = len(pick.formation_cycles)
    interval = cap.interval or rate_cfg.c_rate_step or 1
    modes = sorted(set(pick.modes) | set(cap.modes))

    def cycle_numbers_for(file):
        auto = rate_capability_cycles(formation_count, interval, file.profile_path, cap.modes)
        return sorted(set(pick.formation_cycles) | set(pick.custom_cycles) | set(auto))

    return import_profile_points(
        repo, project_id, "rate", data_dir, cycle_numbers_for, modes, batch_name=batch_name,
        report_cycles=pick.formation_cycles,
    )


def _import_rate_profiles_espec(config: ProjectConfig, rate_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    ESPEC counterpart to import_rate_profiles(). Every row of the
    representative channel's rate_summary() gets imported — formation
    plus every rate_ptns step — keyed by that row's own Rate_Cycle
    number (0-indexed, formation first). A rate-capability test only has
    a couple dozen (Ptn No., Ptn cnt.) rows total (23 here: 2 formation +
    3 reps x 7 rate steps), so unlike Cycle's long continuous run there's
    no need for a custom_cycles/rate_capability subsampling step — every
    step just gets imported.

    IMPORTANT: this means `profiles.formation_cycles`/`custom_cycles`/
    `rate_capability:` in rate.yaml are NOT used for an ESPEC batch —
    those describe Wonatech's 1-indexed ordinal cycle numbering, which
    doesn't apply here. `profiles.formation_plot_cycle` (used by
    plot_rate_formation()) IS still read, but as a literal Rate_Cycle
    value — with 2 formation reps that's 0 or 1, not 1 or 2. Everything
    downstream (plot_rate_formation/plot_rate_profiles) only ever reads
    profile_points back out of the database by (experiment, Rate_Cycle,
    mode), so it needs no further ESPEC-specific code once this has run.
    """
    from battery_analysis.analysis.cycling_common.espec_io import ESPECParser, find_espec_file

    data_dir = config.resolve(config.paths.data_root) / rate_cfg.data_subfolder
    batch_name = data_dir.name
    pick, cap = rate_cfg.profiles, rate_cfg.rate_capability
    modes = sorted(set(pick.modes) | set(cap.modes))

    experiments_df = repo.list_rate_experiments(project_id, batch_name=batch_name)

    by_sample: dict[str, list] = {}
    for ch in rate_cfg.espec_channels:
        by_sample.setdefault(ch.sample, []).append(ch)

    total = 0
    for sample_name, channels in by_sample.items():
        rep = min(channels, key=lambda c: c.replicate)
        if rep.active_mass_g is None:
            continue   # already warned about in import_rate()
        raw_path = find_espec_file(data_dir, rep.channel)
        if raw_path is None:
            continue

        rows = experiments_df[
            (experiments_df["sample"] == sample_name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            print(f"⚠️  {sample_name}: no rate experiment found — run import_rate() first")
            continue
        experiment_id = int(rows.iloc[0]["experiment_id"])
        repo.clear_profile_points(experiment_id)

        parser = ESPECParser(
            raw_path, active_mass_g=rep.active_mass_g,
            formation_ptn=rep.formation_ptn, rate_ptns=rep.rate_ptns,
        )
        rate_df = parser.rate_summary(include_formation=True)
        if rate_df.empty:
            print(f"⚠️  {sample_name}: no rate rows parsed from {raw_path.name}, skipping profiles")
            continue

        formation_rows = []
        for _, row in rate_df.iterrows():
            rate_cycle = int(row["Rate_Cycle"])
            q_ch, v_ch, q_dis, v_dis = parser.profile_for_rate_cycle(rate_cycle)
            for mode, voltage, capacity in [("charge", v_ch, q_ch), ("discharge", v_dis, q_dis)]:
                if voltage is None or mode not in modes:
                    continue
                repo.save_profile_points(experiment_id, rate_cycle, mode, voltage, capacity)
                total += len(voltage)
            if int(row["Ptn No."]) == rep.formation_ptn:
                formation_rows.append((rate_cycle, row))

        for rate_cycle, row in formation_rows:
            q_charge, q_discharge = row["Q_Ch/M [mAh/g]"], row["Q_Dis/M [mAh/g]"]
            ce = 100 * q_discharge / q_charge if q_charge else float("nan")
            print(
                f"   {sample_name} — formation Rate_Cycle {rate_cycle}: "
                f"charge {q_charge:.1f} mAh/g, discharge {q_discharge:.1f} mAh/g, CE {ce:.1f}%"
            )

    if total == 0:
        print("⚠️  No profile points imported.")
    else:
        print(f"✅  Imported {total} profile point(s) into the database.")
    return total
