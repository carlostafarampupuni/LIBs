"""
Curated-Excel FTIR spectrum -> experiment + ftir_point rows.

FTIR has no raw-instrument-file import step in this project (same as the
original ftir.py) — a curated workbook is the only source. Real exports
observed so far use a TWO-row header per sheet (row 1: "Wavenumber\\n(/cm)"
/ "Reflectance" spanning the sample columns; row 2: the actual sample
names, e.g. "bare Cu", "Mxene-Cu") — header_rows=2 in ftir.yaml handles
that; header_rows=1 supports an already-flat sheet whose single header
row IS the sample names directly (e.g. an older curated intermediate).
"""
from __future__ import annotations

import pandas as pd

from battery_analysis.config.schema import ProjectConfig, FTIRBatchConfig
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for


def _read_sheet(excel_path, sheet_cfg) -> pd.DataFrame:
    """
    Return a DataFrame whose FIRST column is the shared x-axis
    (wavenumber) and every other column is named exactly as it appears
    in sheet_cfg.curves[*].column.
    """
    if sheet_cfg.header_rows == 2:
        df = pd.read_excel(excel_path, sheet_name=sheet_cfg.sheet, header=[0, 1])
        flat_cols = []
        for level1, level2 in df.columns:
            level2_str = str(level2)
            if level2_str.startswith("Unnamed") or level2_str.strip().lower() == "nan":
                flat_cols.append(str(level1).strip())
            else:
                flat_cols.append(level2_str.strip())
        df.columns = flat_cols
    else:
        df = pd.read_excel(excel_path, sheet_name=sheet_cfg.sheet, header=0)
        df.columns = [str(c).strip() for c in df.columns]
    return df


def import_ftir(config: ProjectConfig, ftir_cfg: FTIRBatchConfig, repo: ProjectRepository, project_id: int,
                 batch_name: str | None = None) -> int:
    """
    batch_name is the resolved YAML batch KEY (see Project.batch_name() —
    FTIR has no raw folder to derive a name from, so the key itself IS
    the batch name everywhere else in the database/plots/story() filter
    by it). Falls back to the workbook's filename stem only when called
    directly without a Project (batch_name left unset).
    """
    excel_path = config.resolve(config.paths.data_root) / ftir_cfg.excel_path
    if not excel_path.exists():
        print(f"⚠️  FTIR workbook not found: {excel_path}")
        return 0
    if batch_name is None:
        batch_name = excel_path.stem

    sample_colors: dict[str, str] = {}
    total = 0
    for sheet_cfg in ftir_cfg.sheets:
        try:
            df = _read_sheet(excel_path, sheet_cfg)
        except ValueError as e:
            print(f"⚠️  Could not read sheet {sheet_cfg.sheet!r}: {e}")
            continue
        if df.empty or df.shape[1] < 2:
            print(f"⚠️  Sheet {sheet_cfg.sheet!r} has no usable columns — skipping")
            continue
        x_col = df.columns[0]
        wavenumber = pd.to_numeric(df[x_col], errors="coerce")

        for curve in sheet_cfg.curves:
            if curve.column not in df.columns:
                print(f"⚠️  Column {curve.column!r} not found in sheet {sheet_cfg.sheet!r} for sample {curve.sample!r} — skipping")
                continue
            values = pd.to_numeric(df[curve.column], errors="coerce")
            mask = wavenumber.notna() & values.notna()
            if not mask.any():
                print(f"⚠️  No valid rows for {curve.sample!r} in sheet {sheet_cfg.sheet!r}")
                continue

            if curve.sample not in sample_colors:
                global_override = config.sample_override(curve.sample)
                sample_colors[curve.sample] = (
                    (global_override.color if global_override and global_override.color else None)
                    or color_for(len(sample_colors), config.palette or None)
                )
            cell = config.cell_metadata_for(curve.sample)
            sample_id = repo.get_or_create_sample(project_id, curve.sample, color=sample_colors[curve.sample])

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="ftir",
                source_folder=f"{sheet_cfg.sheet}:{curve.column}", batch_name=batch_name,
                replicate_number=1, is_representative=True,
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c, notes=cell.notes,
            )
            repo.clear_ftir_points(experiment_id)
            repo.save_raw_file(experiment_id, str(excel_path))
            repo.save_ftir_points(
                experiment_id, sheet_cfg.spectrum_type,
                wavenumber[mask].to_numpy(), values[mask].to_numpy(),
            )
            total += int(mask.sum())

    if total == 0:
        print("⚠️  No FTIR data imported.")
    else:
        print(f"✅  Imported {total} FTIR point(s) into the database.")
    return total
