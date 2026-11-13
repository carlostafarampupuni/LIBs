"""
Raw files -> EISMeasurement objects, one folder (= one physical cell) at a
time. Uses io.py for the actual file reading; knows about cycle_mapping/
sheet_order but nothing about the database.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .io import collect_eis_files, load_eis_runs
from .models import EISMeasurement


def extract_cycle_from_sheet(sheet_name):
    """Universal extraction for: EISGCD05, EISGCD1, EISpristine, EISFORMATION …"""
    if not isinstance(sheet_name, str):
        return np.nan
    name_upper = sheet_name.upper()
    if "PRISTINE" in name_upper:
        return 0.0
    if "FORMATION" in name_upper:
        return 0.5
    match = re.search(r"\d+", sheet_name)
    if match:
        val = float(match.group())
        return 0.5 if "GCD05" in name_upper else val
    return np.nan


def _filename_mapped_key(stem: str, filename_mapping: Dict[str, str]) -> "str | None":
    """First filename_mapping entry whose substring appears in *stem*
    (case-insensitive), or None if filename_mapping is empty/no match."""
    stem_lower = stem.lower()
    for substring, key in filename_mapping.items():
        if substring.lower() in stem_lower:
            return key
    return None


def collect_measurements(
    folder,
    cycle_mapping: Dict[int, str],
    sheet_order: List[str],
    filename_mapping: "Dict[str, str] | None" = None,
) -> List[EISMeasurement]:
    """
    Read every EIS txt file in *folder* (one physical cell's raw data
    folder) into an EISMeasurement (one per cycle/sweep).
    """
    measurements: List[EISMeasurement] = []
    files = collect_eis_files(folder)
    if not files:
        print(f"⚠️  No EIS files found in {folder}")
        return measurements

    filename_mapping = filename_mapping or {}

    # `position` walks across every RUN (not every file) in folder order,
    # and is what falls through to sheet_order[position] below. For every
    # batch seen so far this is identical to walking the files, because
    # each file has held exactly one run — the exception is a BioLogic
    # .mpt file containing several concatenated cycle sweeps (see io.py),
    # which expands to multiple runs here and so consumes multiple
    # sheet_order positions in one go.
    position = 0

    for txt in files:
        try:
            runs = load_eis_runs(txt)
        except Exception as e:
            print(f"⚠️  Could not load {txt}: {e}")
            continue

        m = re.search(r"(\d+)", txt.stem)
        mapped_key = _filename_mapped_key(txt.stem, filename_mapping) if len(runs) == 1 else None

        for freq, z1, z2 in runs:
            if mapped_key is not None:
                # Explicit filename -> sheet mapping wins whenever it's
                # configured and this file resolved to one run — the
                # only case where "this filename means this sheet" is
                # unambiguous. Works regardless of naming convention
                # (or lack of one), unlike cycle_mapping's embedded-digit
                # requirement.
                cycle = mapped_key
            elif len(runs) == 1 and m and int(m.group(1)) in cycle_mapping:
                # Same behaviour as before: a lone run whose filename digit
                # matches cycle_mapping is labelled from that mapping.
                cycle = cycle_mapping[int(m.group(1))]
            else:
                if position < len(sheet_order):
                    cycle = sheet_order[position]
                else:
                    print(f"⚠️  More EIS runs than sheet_order entries — skipping {txt.name} (run {position + 1})")
                    position += 1
                    continue

            measurements.append(
                EISMeasurement(
                    cycle_number=cycle,
                    sheet_name=None,  # filled in by importer once sheet_names is applied
                    freq=np.asarray(freq, dtype=float),
                    z_real=np.asarray(z1, dtype=float),
                    z_imag=np.asarray(z2, dtype=float),
                    source_file=txt,
                )
            )
            position += 1

    return measurements
