"""
Raw *_Capacity.csv files -> experiment + cycle_point rows in the database.

Mirrors the EIS importer's shape: auto-discover files (via
cycling_common.discovery), resolve each sample's chemistry metadata
(cell_defaults + any global override), create/update one `experiment` row
per (sample, replicate file), then write the per-cycle capacity/CE series.
"""
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


def import_cycle(config: ProjectConfig, cycle_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Auto-discover every *_Capacity.csv under data_root/cycle_cfg.data_subfolder,
    import it, and return the number of cycle points imported.

    A batch with any `espec_channels:` entries is imported entirely via
    the ESPEC path instead (see _import_cycle_espec) — the two raw
    formats never mix within one batch.
    """
    if cycle_cfg.espec_channels:
        return _import_cycle_espec(config, cycle_cfg, repo, project_id)

    data_dir = config.resolve(config.paths.data_root) / cycle_cfg.data_subfolder
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
            series = load_series(sample.name, file.capacity_path)
            if series is None:
                print(f"⚠️  {file.capacity_path.name}: no valid rows, skipping")
                continue

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="cycle",
                source_folder=file.capacity_path.name, batch_name=batch_name,
                replicate_number=file.replicate_number,
                is_representative=(file.replicate_number == sample.default_representative.replicate_number),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_experiment_date(config, file.capacity_path),
                notes=cell.notes,
            )
            repo.clear_cycle_points(experiment_id)
            repo.save_raw_file(experiment_id, str(file.capacity_path))
            if file.profile_path is not None:
                repo.save_raw_file(experiment_id, str(file.profile_path))

            retention_pct = 100 * series.capacity_discharge / series.capacity_discharge[0]
            repo.save_cycle_points(
                experiment_id, series.cycle_number, series.capacity_charge,
                series.capacity_discharge, series.ce_pct, retention_pct,
            )
            total += len(series.cycle_number)

    if total == 0:
        print("⚠️  No cycle data imported.")
    else:
        print(f"✅  Imported {total} cycle point(s) into the database.")
    return total


def _import_cycle_espec(config: ProjectConfig, cycle_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    ESPEC-format counterpart to import_cycle() above — same experiment/
    cycle_point rows, same color/chemistry resolution, same per-file
    self-healing (one bad channel doesn't cost you the rest of the
    batch), just reading espec_channels: instead of auto-discovering
    *_Capacity.csv files. See ESPECChannelConfig (config/schema.py) and
    cycling_common/espec_io.py's module docstring.
    """
    from battery_analysis.analysis.cycling_common.espec_io import (
        ESPECParser, find_espec_file, cycle_series_from_espec,
    )

    data_dir = config.resolve(config.paths.data_root) / cycle_cfg.data_subfolder
    batch_name = data_dir.name

    # Group channels by sample, preserving YAML declaration order — the
    # same grouping discover_cycling_files() would produce from filenames,
    # just built from espec_channels: instead since ESPEC filenames don't
    # carry a sample name.
    samples_order: list[str] = []
    by_sample: dict[str, list] = {}
    for ch in cycle_cfg.espec_channels:
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
                    f"⚠️  {ch.channel} ({sample_name}): no active_mass_g set in cycle.yaml — "
                    f"skipping (can't convert mAh -> mAh/g without it, and guessing would silently "
                    f"corrupt every downstream number)"
                )
                continue
            raw_path = find_espec_file(data_dir, ch.channel)
            if raw_path is None:
                print(f"⚠️  {ch.channel}: no *_All.csv found under {data_dir}, skipping")
                continue

            try:
                parser = ESPECParser(
                    raw_path, active_mass_g=ch.active_mass_g,
                    formation_ptn=ch.formation_ptn, cycle_ptn=ch.cycle_ptn,
                )
            except (ValueError, OSError) as e:
                print(f"⚠️  {ch.channel}: could not parse {raw_path.name} ({e}), skipping")
                continue

            series = cycle_series_from_espec(parser, sample_name)
            if series is None or len(series.cycle_number) == 0:
                print(f"⚠️  {ch.channel}: no valid cycle rows parsed, skipping")
                continue

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="cycle",
                source_folder=raw_path.name, batch_name=batch_name,
                replicate_number=ch.replicate,
                is_representative=(ch.replicate == default_replicate),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_experiment_date(config, raw_path),
                notes=cell.notes,
            )
            repo.clear_cycle_points(experiment_id)
            repo.save_raw_file(experiment_id, str(raw_path))

            retention_pct = 100 * series.capacity_discharge / series.capacity_discharge[0]
            repo.save_cycle_points(
                experiment_id, series.cycle_number, series.capacity_charge,
                series.capacity_discharge, series.ce_pct, retention_pct,
            )
            total += len(series.cycle_number)

    if total == 0:
        print("⚠️  No cycle data imported.")
    else:
        print(f"✅  Imported {total} cycle point(s) into the database.")
    return total


def import_cycle_profiles(config: ProjectConfig, cycle_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Voltage-vs-capacity profile points for this batch's formation_cycles
    + custom_cycles (see profiles: in cycle.yaml) — pulled straight out
    of each sample's *_Cycle.csv. Run import_cycle() first: this keys its
    rows to the experiment import_cycle() already created for each
    sample, it doesn't create its own.

    ESPEC batches (espec_channels: set) take a separate path — see
    _import_cycle_profiles_espec — since there's no *_Cycle.csv file to
    read; the same _All.csv already read by import_cycle() has the
    voltage profile in it too.
    """
    if cycle_cfg.espec_channels:
        return _import_cycle_profiles_espec(config, cycle_cfg, repo, project_id)

    from battery_analysis.analysis.cycling_common.profiles import import_profile_points

    data_dir = config.resolve(config.paths.data_root) / cycle_cfg.data_subfolder
    batch_name = data_dir.name
    pick = cycle_cfg.profiles
    cycle_numbers = sorted(set(pick.formation_cycles) | set(pick.custom_cycles))
    if not cycle_numbers:
        print("⚠️  No formation_cycles/custom_cycles set under profiles: in cycle.yaml — nothing to import.")
        return 0
    return import_profile_points(
        repo, project_id, "cycle", data_dir, cycle_numbers, pick.modes, batch_name=batch_name,
        report_cycles=pick.formation_cycles,
    )


def _import_cycle_profiles_espec(config: ProjectConfig, cycle_cfg, repo: ProjectRepository, project_id: int) -> int:
    """ESPEC counterpart to import_cycle_profiles(), via ESPECParser.profile_for_cycle()."""
    from battery_analysis.analysis.cycling_common.espec_io import ESPECParser, find_espec_file

    data_dir = config.resolve(config.paths.data_root) / cycle_cfg.data_subfolder
    batch_name = data_dir.name
    pick = cycle_cfg.profiles
    cycle_numbers = sorted(set(pick.formation_cycles) | set(pick.custom_cycles))
    if not cycle_numbers:
        print("⚠️  No formation_cycles/custom_cycles set under profiles: in cycle.yaml — nothing to import.")
        return 0

    experiments_df = repo.list_cycle_experiments(project_id, batch_name=batch_name)

    # One representative channel per sample (lowest replicate), same rule
    # as the Wonatech path / discover_cycling_files' default_representative.
    by_sample: dict[str, list] = {}
    for ch in cycle_cfg.espec_channels:
        by_sample.setdefault(ch.sample, []).append(ch)

    total = 0
    for sample_name, channels in by_sample.items():
        rep = min(channels, key=lambda c: c.replicate)
        if rep.active_mass_g is None:
            continue   # already warned about in import_cycle()
        raw_path = find_espec_file(data_dir, rep.channel)
        if raw_path is None:
            continue

        rows = experiments_df[
            (experiments_df["sample"] == sample_name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            print(f"⚠️  {sample_name}: no cycle experiment found — run import_cycle() first")
            continue
        experiment_id = int(rows.iloc[0]["experiment_id"])
        repo.clear_profile_points(experiment_id)

        parser = ESPECParser(
            raw_path, active_mass_g=rep.active_mass_g,
            formation_ptn=rep.formation_ptn, cycle_ptn=rep.cycle_ptn,
        )
        for cycle_num in cycle_numbers:
            q_ch, v_ch, q_dis, v_dis = parser.profile_for_cycle(cycle_num)
            for mode, voltage, capacity in [("charge", v_ch, q_ch), ("discharge", v_dis, q_dis)]:
                if voltage is None or mode not in pick.modes:
                    continue
                repo.save_profile_points(experiment_id, cycle_num, mode, voltage, capacity)
                total += len(voltage)

        for cycle_num in pick.formation_cycles:
            q_ch, _, q_dis, _ = parser.profile_for_cycle(cycle_num)
            if q_ch is None or q_dis is None:
                continue
            q_charge, q_discharge = float(np.max(q_ch)), float(np.max(q_dis))
            ce = 100 * q_discharge / q_charge if q_charge else float("nan")
            print(
                f"   {sample_name} — formation cycle {cycle_num}: "
                f"charge {q_charge:.1f} mAh/g, discharge {q_discharge:.1f} mAh/g, CE {ce:.1f}%"
            )

    if total == 0:
        print("⚠️  No profile points imported.")
    else:
        print(f"✅  Imported {total} profile point(s) into the database.")
    return total
