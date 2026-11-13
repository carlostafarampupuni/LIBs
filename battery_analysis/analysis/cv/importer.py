"""
Curated-Excel CV -> experiment + cv_point rows.

Real/toy workbooks share the same shape: one sheet per cell, columns
alternating voltage/current per scan rate — e.g. "Ref1"/0.1, "Ref2"/0.2,
... ("Ref1" = voltage at scan rate 0.1, the literal float 0.1 = its
matching current column). voltage_prefix + scan_rates (in cv.yaml)
reconstruct that pairing positionally (1st scan rate -> "{prefix}1", ...)
without listing every column by hand.
"""
from __future__ import annotations

import pandas as pd

from battery_analysis.config.schema import ProjectConfig, CVBatchConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for


def _find_current_col(columns, scan_rate: float):
    for c in columns:
        if isinstance(c, (int, float)) and abs(float(c) - scan_rate) < 1e-9:
            return c
    return None


def import_cv(config: ProjectConfig, cv_cfg: CVBatchConfig, repo: ProjectRepository, project_id: int,
              batch_name: str | None = None) -> int:
    """batch_name is the resolved YAML batch KEY — see the matching note
    in analysis/ftir/importer.py.import_ftir()."""
    excel_path = config.resolve(config.paths.data_root) / cv_cfg.excel_path
    if not excel_path.exists():
        print(f"⚠️  CV workbook not found: {excel_path}")
        return 0
    if batch_name is None:
        batch_name = excel_path.stem

    sample_colors: dict[str, str] = {}
    total = 0
    for cell_cfg in cv_cfg.cells:
        try:
            df = pd.read_excel(excel_path, sheet_name=cell_cfg.sheet)
        except ValueError as e:
            print(f"⚠️  Could not read sheet {cell_cfg.sheet!r}: {e}")
            continue

        if cell_cfg.sample not in sample_colors:
            global_override = config.sample_override(cell_cfg.sample)
            sample_colors[cell_cfg.sample] = (
                cell_cfg.color
                or (global_override.color if global_override and global_override.color else None)
                or color_for(len(sample_colors), config.palette or None)
            )
        cell_meta = config.cell_metadata_for(cell_cfg.sample)
        sample_id = repo.get_or_create_sample(project_id, cell_cfg.sample, color=sample_colors[cell_cfg.sample])

        experiment_id = repo.get_or_create_experiment(
            project_id, sample_id, technique="cv",
            source_folder=cell_cfg.sheet, batch_name=batch_name,
            replicate_number=1, is_representative=True,
            cathode=cell_meta.cathode, anode=cell_meta.anode, cell_type=cell_meta.cell_type,
            electrolyte=cell_meta.electrolyte, additives=cell_meta.additives,
            temperature_c=cell_meta.temperature_c, notes=cell_meta.notes,
        )
        repo.clear_cv_points(experiment_id)
        repo.save_raw_file(experiment_id, str(excel_path))

        for i, scan_rate in enumerate(cell_cfg.scan_rates, start=1):
            voltage_col = f"{cell_cfg.voltage_prefix}{i}"
            current_col = _find_current_col(df.columns, scan_rate)
            if voltage_col not in df.columns or current_col is None:
                print(f"⚠️  {cell_cfg.sheet!r}: no column pair for scan rate {scan_rate} "
                      f"(expected {voltage_col!r} + ~{scan_rate}) — skipping")
                continue
            voltage = pd.to_numeric(df[voltage_col], errors="coerce")
            current = pd.to_numeric(df[current_col], errors="coerce")
            mask = voltage.notna() & current.notna()
            if not mask.any():
                continue
            repo.save_cv_points(experiment_id, scan_rate, voltage[mask].to_numpy(), current[mask].to_numpy())
            total += int(mask.sum())

    if total == 0:
        print("⚠️  No CV data imported.")
    else:
        print(f"✅  Imported {total} CV point(s) into the database.")
    return total
