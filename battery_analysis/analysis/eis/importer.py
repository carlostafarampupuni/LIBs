"""
Raw files -> experiment + measurement rows in the database.

Samples and their replicate folders are AUTO-DISCOVERED from
data_root/<techniques.eis.data_subfolder> — you never type folder names.
config.yaml only supplies overrides (rename a sample, force a specific
color, pick a non-default representative folder, override chemistry).

For each discovered (sample, folder) pair — i.e. every physical cell —
this:
  1. Resolves that sample's chemistry/cell metadata (cell_defaults merged
     with any per-sample override in config.yaml).
  2. Creates or updates one `experiment` row: which sample, which raw
     folder, replicate number (auto-detected from the folder's trailing
     digit), cathode/anode/cell_type/electrolyte/additives/temperature,
     and a date (from config.experiment_dates, else the earliest raw
     file's modified time).
  3. Reads every cycle's raw file into an eis_measurement + spectrum.
"""
from __future__ import annotations

from datetime import datetime, timezone

from battery_analysis.config.schema import ProjectConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import discover_samples, color_for

from .io import earliest_mtime, is_eis_file
from .parser import collect_measurements


def _resolve_experiment_date(config: ProjectConfig, folder: str, eis_root) -> str | None:
    if folder in config.experiment_dates:
        return config.experiment_dates[folder]
    mtime = earliest_mtime(eis_root / folder)
    if mtime is None:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).date().isoformat()


def import_eis(config: ProjectConfig, eis_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Auto-discover every sample folder under data_root/eis_cfg.data_subfolder,
    import its raw files, and return the number of measurements imported.

    Safe to re-run: each folder's existing measurements are cleared before
    the fresh import, so re-running after adding new cycle files doesn't
    create duplicates, and editing cell_defaults/notes in config.yaml and
    re-running updates the experiment record in place.
    """
    eis_root = config.resolve(config.paths.data_root) / eis_cfg.data_subfolder
    batch_name = eis_root.name  # e.g. "Batch1" — derived, never typed

    discovered = discover_samples(eis_root, file_matches=is_eis_file)
    if not discovered:
        print(f"⚠️  No EIS-looking folders found under {eis_root}")
        return 0

    # exclude: — drop what was imported for these samples/cells earlier, so
    # fits, plots and exports of this batch no longer see them either.
    excluded = set(eis_cfg.exclude)
    if eis_cfg.samples:                       # samples: list — anything not listed is hidden too
        excluded |= {d.base_name for d in discovered if d.base_name not in eis_cfg.samples}
    if excluded:
        prior = repo.list_eis_measurements(project_id, batch_name=batch_name)
        gone = prior[prior["sample"].isin(excluded) | prior["source_folder"].isin(excluded)]
        for experiment_id in gone["experiment_id"].unique():
            repo.clear_eis_measurements(int(experiment_id))
        if not gone.empty:
            print(f"🙈  Excluded (eis.yaml exclude:): {', '.join(sorted(gone['source_folder'].unique()))}")

    total = 0
    for color_idx, sample in enumerate(discovered):
        if sample.base_name in excluded:
            continue
        global_override = config.sample_override(sample.base_name)   # color/chemistry — shared config.yaml
        batch_override = eis_cfg.sample_override(sample.base_name)    # folders/plot — this batch's eis*.yaml
        sample_name = sample.base_name
        color = (
            (global_override.color if global_override and global_override.color else None)
            or color_for(color_idx, config.palette or None)
        )
        representative = (
            (batch_override.plot[0] if batch_override and batch_override.plot else None)
            or sample.default_representative
        )
        cell = config.cell_metadata_for(sample_name)

        sample_id = repo.get_or_create_sample(project_id, sample_name, color=color)

        folders = (
            batch_override.folders if batch_override and batch_override.folders else sample.folder_names
        )
        folders = [f for f in folders if f not in excluded]
        if not folders:
            continue
        if representative not in folders:
            representative = folders[0]
        for replicate in sample.replicates:
            if replicate.folder_name not in folders:
                continue

            measurements = collect_measurements(
                replicate.path, eis_cfg.cycle_mapping, eis_cfg.sheet_order,
                filename_mapping=eis_cfg.filename_mapping,
            )
            if not measurements:
                continue

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="eis",
                source_folder=replicate.folder_name, batch_name=batch_name,
                replicate_number=replicate.replicate_number,
                is_representative=(replicate.folder_name == representative),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_resolve_experiment_date(config, replicate.folder_name, eis_root),
                notes=cell.notes,
            )
            repo.clear_eis_measurements(experiment_id)

            for meas in measurements:
                sheet_name = eis_cfg.sheet_names.get(meas.cycle_number, meas.cycle_number)
                repo.save_raw_file(experiment_id, str(meas.source_file))
                repo.save_eis_measurement(
                    experiment_id, sheet_name=sheet_name, cycle_number=meas.cycle_number,
                    freq=meas.freq, z_real=meas.z_real, z_imag=meas.z_imag,
                )
                total += 1

    if total == 0:
        print("⚠️  No EIS data matched the requested cycles. Nothing imported.")
    else:
        print(f"✅  Imported {total} EIS measurement(s) into the database.")
    return total
