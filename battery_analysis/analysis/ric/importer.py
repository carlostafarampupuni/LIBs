"""
Raw *_Capacity.csv files -> experiment + ric_point rows in the database.

RIC (reversible/irreversible capacity) is a DERIVED analysis of the exact
same *_Capacity.csv files Cycle already reads — same discovery
(cycling_common.discovery), same per-cycle Q_Ch/M and Q_Dis/M columns
(cycling_common.io.read_capacity_csv). Nothing new is asked of the raw
data; RIC just computes three extra series from the columns that are
already there:

    RIC         = (Cc - Cd) / Cd                      overall irreversible capacity
    RIC_disconn = (Cd[n-1] - Cd[n]) / Cd[n-1]          active-material disconnection
    RIC_sei     = (Cc[n] - Cd[n-1]) / Cd[n-1]          SEI growth

Ported from the original ric.py's load_and_compute().
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from battery_analysis.config.schema import ProjectConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for
from battery_analysis.analysis.cycling_common.discovery import discover_cycling_files
from battery_analysis.analysis.cycling_common.io import read_capacity_csv, CyclingFileFormatError


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


def compute_ric(cycle: np.ndarray, cc: np.ndarray, cd: np.ndarray):
    """Return (ric, ric_disconn, ric_sei) — the latter two one element
    shorter than cycle/cc/cd (a between-consecutive-cycles quantity)."""
    ric = (cc - cd) / cd
    ric_disconn = (cd[:-1] - cd[1:]) / cd[:-1]
    ric_sei = (cc[1:] - cd[:-1]) / cd[:-1]
    return ric, ric_disconn, ric_sei


def import_ric(config: ProjectConfig, ric_cfg, repo: ProjectRepository, project_id: int) -> int:
    """
    Auto-discover every *_Capacity.csv under data_root/ric_cfg.data_subfolder
    (the SAME files import_cycle() reads for the matching batch), compute
    RIC/RIC_disconn/RIC_sei for each, and return the number of points imported.
    """
    data_dir = config.resolve(config.paths.data_root) / ric_cfg.data_subfolder
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
            try:
                df = read_capacity_csv(file.capacity_path)
            except CyclingFileFormatError as e:
                print(f"⚠️  {e}")
                continue
            if df.empty:
                print(f"⚠️  {file.capacity_path.name}: not enough rows for RIC, skipping")
                continue

            # A zero (or negative) discharge capacity is an aborted/incomplete
            # cycle, not a real measurement — every RIC formula divides by
            # Cd, so leaving one in produces inf/-inf instead of a number.
            # These are almost always the LAST row of the file, but drop
            # them wherever they occur rather than assuming that.
            n_before = len(df)
            df = df[df["Q_Discharge"] > 0].reset_index(drop=True)
            if len(df) < n_before:
                print(f"⚠️  {file.capacity_path.name}: dropped {n_before - len(df)} "
                      f"zero/negative-discharge row(s) before computing RIC")
            if len(df) < 2:
                print(f"⚠️  {file.capacity_path.name}: not enough valid rows for RIC, skipping")
                continue

            cycle = df["Cycle"].to_numpy()
            cc = df["Q_Charge"].to_numpy()
            cd = df["Q_Discharge"].to_numpy()
            ric, ric_disconn, ric_sei = compute_ric(cycle, cc, cd)

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="ric",
                source_folder=file.capacity_path.name, batch_name=batch_name,
                replicate_number=file.replicate_number,
                is_representative=(file.replicate_number == sample.default_representative.replicate_number),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=_experiment_date(config, file.capacity_path),
                notes=cell.notes,
            )
            repo.clear_ric_points(experiment_id)
            repo.save_raw_file(experiment_id, str(file.capacity_path))
            repo.save_ric_points(experiment_id, cycle, cc, cd, ric, ric_disconn, ric_sei)
            total += len(cycle)

    if total == 0:
        print("⚠️  No RIC data imported.")
    else:
        print(f"✅  Imported {total} RIC point(s) into the database.")
    return total
