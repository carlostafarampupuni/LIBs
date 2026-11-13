"""
Raw *_Cycle.csv files -> experiment + dqdv curve + peak rows.

Reuses cycling_common's file discovery (the same *_Capacity.csv/
*_Cycle.csv pairs Cycle reads) — dQ/dV needs the *_Cycle.csv profile
file specifically, and computes its own independent experiment records
(technique='dqdv') so it doesn't depend on Cycle having been imported
first.
"""
from __future__ import annotations

from datetime import datetime, timezone

from battery_analysis.config.schema import ProjectConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for
from battery_analysis.analysis.cycling_common.discovery import discover_cycling_files

from .fitting import extract_cycle_dqdv
from .analyzer import find_peaks


def _experiment_date(config: ProjectConfig, path) -> str | None:
    if path.name in config.experiment_dates:
        return config.experiment_dates[path.name]
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    except OSError:
        return None


def import_dqdv(config: ProjectConfig, dqdv_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    For every sample's representative *_Cycle.csv profile file, compute
    dQ/dV for each configured cycle/mode and store the curve + peaks.
    Returns the number of (cycle, mode) curves imported.
    """
    data_dir = config.resolve(config.paths.data_root) / dqdv_cfg.data_subfolder
    batch_name = data_dir.name

    discovered = discover_cycling_files(data_dir)
    if not discovered:
        print(f"⚠️  No *_Cycle.csv files found under {data_dir}")
        return 0

    total = 0
    for color_idx, sample in enumerate(discovered):
        override = config.sample_override(sample.name)
        color = (
            (override.color if override and override.color else None)
            or color_for(color_idx, config.palette or None)
        )
        cell = config.cell_metadata_for(sample.name)
        sample_id = repo.get_or_create_sample(project_id, sample.name, color=color)

        file = sample.default_representative
        if file is None or file.profile_path is None:
            print(f"⚠️  {sample.name}: no *_Cycle.csv profile file found, skipping")
            continue

        experiment_id = repo.get_or_create_experiment(
            project_id, sample_id, technique="dqdv",
            source_folder=file.capacity_path.name, batch_name=batch_name,
            replicate_number=file.replicate_number, is_representative=True,
            cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
            electrolyte=cell.electrolyte, additives=cell.additives,
            temperature_c=cell.temperature_c,
            experiment_date=_experiment_date(config, file.capacity_path),
            notes=cell.notes,
        )
        repo.clear_dqdv(experiment_id)
        repo.save_raw_file(experiment_id, str(file.profile_path))

        for cycle_num in dqdv_cfg.cycles:
            for mode in dqdv_cfg.modes:
                voltage, dqdv = extract_cycle_dqdv(
                    file.profile_path, cycle_num, mode, dqdv_cfg.smoothing_window
                )
                if voltage is None:
                    continue
                repo.save_dqdv_curve(experiment_id, cycle_num, mode, voltage, dqdv)
                peaks = find_peaks(voltage, dqdv, mode, prominence=dqdv_cfg.peak_prominence)
                repo.save_dqdv_peaks(experiment_id, cycle_num, mode, [(p.voltage, p.intensity) for p in peaks])
                total += 1

    if total == 0:
        print("⚠️  No dQ/dV curves computed.")
    else:
        print(f"✅  Computed {total} dQ/dV curve(s) into the database.")
    return total
