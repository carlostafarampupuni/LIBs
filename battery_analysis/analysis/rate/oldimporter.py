"""Raw *_Capacity.csv files (rate-capability test) -> experiment + rate_point rows."""
from __future__ import annotations

from datetime import datetime, timezone

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

        for file in sample.files:
            series = load_series(sample.name, file.capacity_path, electrode_type=rate_cfg.electrode_type)
            if series is None:
                print(f"⚠️  {file.capacity_path.name}: no valid rows, skipping")
                continue

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="rate",
                source_folder=file.capacity_path.name, batch_name=batch_name,
                replicate_number=file.replicate_number,
                is_representative=(file.replicate_number == sample.default_representative.replicate_number),
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

    Not yet implemented for ESPEC batches (espec_channels: set) — prints
    a notice and returns 0 rather than crashing; the summary/CE numbers
    from import_rate() are unaffected, only voltage-profile figures for
    that batch are unavailable for now.
    """
    if rate_cfg.espec_channels:
        print("⚠️  import_rate_profiles(): ESPEC-format profile import isn't implemented yet — skipping.")
        return 0

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
