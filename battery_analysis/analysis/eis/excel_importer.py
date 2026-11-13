"""Importer for the SiO2 supercapacitor EIS Excel exports.

The full instrument workbooks contain several techniques (FRA/Nyquist,
CV, charge-discharge, etc.). This importer selects the Nyquist worksheet,
keeps only its ordered Z'/imaginary-coordinate pairs, and writes the
spectra through the existing EIS repository API. The exported workbook
does not include frequency, so frequency points are stored as SQL NULL
and must not be used for circuit fitting, Bode plots, or DRT.

Install this file as:
    battery_analysis/analysis/eis/excel_importer.py

Then dispatch to ``import_excel_eis`` from the existing
``battery_analysis/analysis/eis/importer.py`` when the configured EIS
batch points at this workbook layout. See the accompanying eis.yaml.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import load_workbook

from battery_analysis.discovery import color_for


CELL_RE = re.compile(r"^CELL\s*(\d+)\s*$", re.IGNORECASE)
TEMP_PREFIXES = ("~$", ".")


def _finite_number(value: Any) -> float | None:
    """Convert an Excel cell to a finite float, or None for blank/text/NaN."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _worksheet_header(ws) -> tuple[int | None, str]:
    """Find the Nyquist metadata row and return its row number and text."""
    snippets: list[str] = []
    header_row = None
    for row_number, row in enumerate(ws.iter_rows(min_row=1, max_row=8, values_only=True), start=1):
        text = " ".join(str(value) for value in row if value is not None).strip()
        snippets.append(text)
        normalized = text.lower().replace("″", "''").replace("′", "'")
        if "nyquist" in normalized and "z'" in normalized and ("z''" in normalized or "im(z)" in normalized):
            header_row = row_number
    return header_row, " | ".join(snippets)


def _parse_nyquist_sheet(ws) -> dict[str, Any] | None:
    """Read the dataset's two-column Nyquist export without touching CV/GCD tabs."""
    header_row, header_text = _worksheet_header(ws)
    if header_row is None:
        return None

    # The supplied workbooks store Z' in column A and the plotted
    # imaginary coordinate (-Z'') in column B, with labels in a metadata
    # row above the numeric data. Keep acquisition/export order; never
    # sort by either impedance coordinate.
    points: list[tuple[float, float]] = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if len(row) < 2:
            continue
        x_value = _finite_number(row[0])
        y_value = _finite_number(row[1])
        if x_value is None or y_value is None:
            continue
        points.append((x_value, y_value))

    if len(points) < 3:
        return None

    # Existing database/plot code expects signed Im(Z), then renders the
    # -Z'' axis. The workbook's second coordinate is labelled -Z''.
    # Negate exactly once to store Im(Z); preserve any negative values.
    z_real = np.asarray([x for x, _ in points], dtype=float)
    z_imag = -np.asarray([y for _, y in points], dtype=float)
    freq = np.full(len(points), np.nan, dtype=float)
    return {
        "sheet_name": "Nyquist",
        "freq": freq,
        "z_real": z_real,
        "z_imag": z_imag,
        "point_count": len(points),
        "header": header_text,
    }


def read_excel_eis(path: str | Path) -> dict[str, Any] | None:
    """Return one Nyquist spectrum from a multi-sheet workbook.

    The workbook's other sheets are deliberately ignored. If more than
    one Nyquist-like sheet exists, the sheet with the most valid points
    is selected; ties are resolved in workbook order and reported.
    """
    path = Path(path)
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        candidates = []
        for ws in wb.worksheets:
            result = _parse_nyquist_sheet(ws)
            if result is not None:
                candidates.append((result["point_count"], ws.title, result))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            print(f"⚠️ {path.name}: multiple Nyquist sheets tie; using {candidates[0][1]!r} in workbook order.")
        return candidates[0][2]
    finally:
        wb.close()


def _choose_workbook(cell_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    """Choose the full analysis workbook over a duplicate FRA-only export."""
    candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
    for path in sorted(cell_dir.glob("*.xlsx")):
        if path.name.startswith(TEMP_PREFIXES):
            continue
        try:
            spectrum = read_excel_eis(path)
        except Exception as exc:
            print(f"⚠️ Could not read {path}: {exc}")
            continue
        if spectrum is None:
            continue

        # Main analysis workbooks include the Nyquist/FRA data alongside
        # CV and charge-discharge tabs. A separate FRA-only workbook is a
        # duplicate export. Prefer the workbook with more populated
        # sheets, then prefer filenames containing 'analisis' but not
        # the standalone 'FRA' suffix. Do not use modification time.
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet_count = sum(1 for ws in wb.worksheets if ws.max_row > 2)
        finally:
            wb.close()
        standalone_fra = int("fra" in path.stem.lower() and "analisis" in path.stem.lower())
        score = sheet_count * 10 - standalone_fra
        candidates.append((score, path.name, path, spectrum))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        names = ", ".join(item[2].name for item in candidates if item[0] == candidates[0][0])
        print(f"⚠️ {cell_dir}: ambiguous workbook choice ({names}); skipped to avoid duplicate/incorrect import.")
        return None
    if len(candidates) > 1:
        print(f"ℹ️ {cell_dir.name}: selected {candidates[0][2].name}; ignored duplicate workbook(s).")
    return candidates[0][2], candidates[0][3]


def has_excel_eis_layout(eis_root: str | Path) -> bool:
    """Recognize the nested composition/CELL/workbook layout in Batch2."""
    root = Path(eis_root)
    if not root.is_dir():
        return False
    for sample_dir in root.iterdir():
        if not sample_dir.is_dir() or sample_dir.name.startswith("."):
            continue
        for cell_dir in sample_dir.iterdir():
            if cell_dir.is_dir() and CELL_RE.fullmatch(cell_dir.name.strip()):
                if any(p.suffix.lower() == ".xlsx" and not p.name.startswith(TEMP_PREFIXES)
                       for p in cell_dir.iterdir() if p.is_file()):
                    return True
    return False


def import_excel_eis(config, eis_cfg, repo, project_id: int) -> int:
    """Import one workbook spectrum per physical cell under EIS/Batch2.

    Expected layout::

        Data/EIS/Batch2/<composition>/CELL <n>/*.xlsx

    A single representative cell (the lowest numbered cell folder) is
    marked for the existing Nyquist plotter. All cell spectra are kept in
    the database. The importer does not infer a frequency axis or fit a
    circuit.
    """
    eis_root = config.resolve(config.paths.data_root) / eis_cfg.data_subfolder
    batch_name = eis_root.name
    if not eis_root.is_dir():
        raise FileNotFoundError(f"EIS workbook folder does not exist: {eis_root}")

    total = 0
    sample_dirs = [p for p in sorted(eis_root.iterdir()) if p.is_dir() and not p.name.startswith(".")]
    for color_index, sample_dir in enumerate(sample_dirs):
        cell_dirs = []
        for child in sample_dir.iterdir():
            if child.is_dir():
                match = CELL_RE.fullmatch(child.name.strip())
                if match:
                    cell_dirs.append((int(match.group(1)), child))
        cell_dirs.sort(key=lambda item: (item[0], item[1].name.lower()))
        if not cell_dirs:
            continue

        global_override = config.sample_override(sample_dir.name)
        batch_override = eis_cfg.sample_override(sample_dir.name)
        color = (
            global_override.color if global_override and global_override.color
            else color_for(color_index, config.palette or None)
        )
        sample_id = repo.get_or_create_sample(project_id, sample_dir.name, color=color)
        cell = config.cell_metadata_for(sample_dir.name)
        representative_folder = (
            batch_override.plot[0]
            if batch_override and batch_override.plot
            else cell_dirs[0][1].name
        )
        allowed_folders = set(batch_override.folders) if batch_override and batch_override.folders else None

        for replicate_number, cell_dir in cell_dirs:
            if allowed_folders is not None and cell_dir.name not in allowed_folders:
                continue
            chosen = _choose_workbook(cell_dir)
            if chosen is None:
                print(f"⚠️ No usable multi-sheet EIS workbook found in {cell_dir}")
                continue
            workbook_path, spectrum = chosen

            experiment_id = repo.get_or_create_experiment(
                project_id, sample_id, technique="eis",
                source_folder=f"{sample_dir.name}/{cell_dir.name}",
                batch_name=batch_name,
                replicate_number=replicate_number,
                is_representative=(cell_dir.name == representative_folder),
                cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
                electrolyte=cell.electrolyte, additives=cell.additives,
                temperature_c=cell.temperature_c,
                experiment_date=datetime.fromtimestamp(
                    workbook_path.stat().st_mtime, tz=timezone.utc
                ).date().isoformat(),
                notes=cell.notes,
            )
            repo.clear_eis_measurements(experiment_id)
            repo.save_raw_file(experiment_id, str(workbook_path))
            repo.save_eis_measurement(
                experiment_id,
                sheet_name=spectrum["sheet_name"],
                cycle_number="1",
                freq=spectrum["freq"],
                z_real=spectrum["z_real"],
                z_imag=spectrum["z_imag"],
            )
            total += 1

    if total:
        print(f"✅ Imported {total} workbook EIS spectrum(s) into database batch {batch_name}.")
        print("ℹ️ Frequency was absent in these Excel exports; fitting, Bode plots, and DRT are disabled for this batch.")
    else:
        print(f"⚠️ No Nyquist spectra imported from {eis_root}.")
    return total
