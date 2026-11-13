"""
Low-level raw-file IO for EIS. Nothing here knows about samples,
experiments, or the database — just "is this an EIS file" and
"read this file into (freq, z_real, z_imag)".

Two raw formats are supported, auto-detected per file (no config
needed — a batch's folder can even mix both):

  - This project's plain export: 3 tab-separated columns (frequency,
    Z_real, Z_imag), header on row 0, no metadata block. Filenames seen
    so far: "defaultdata_N.txt", "3.0 V.txt", etc.

  - BioLogic EC-Lab .mpt ASCII export: a metadata header block (its
    exact length given by a "Nb header lines : N" line near the top —
    N counts through the column-names line itself), then a wide
    tab-separated data table with named columns (freq/Hz, Re(Z)/Ohm,
    -Im(Z)/Ohm, |Z|/Ohm, Phase(Z)/deg, time/s, ...). Every genuine
    export of this kind starts with the exact line "EC-Lab ASCII FILE",
    which is what's used to recognise it.

    A single .mpt file can contain several repeated EIS frequency
    sweeps concatenated back to back (EC-Lab re-runs the same
    frequency list for each "cycle" of the protocol) — these are
    distinguished by the file's own "cycle number" data column, e.g.
    52 rows at cycle number 1.0, then 52 more at 2.0, then 3.0. Each
    is a separate Nyquist spectrum and must be split apart, not
    plotted as one continuous 156-point trace. load_eis_runs() below
    does that split; load_eis_file() stays as a "just give me one
    spectrum" convenience that returns the first run.
"""
from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

_HEADER_LINE_COUNT_RE = re.compile(r"Nb header lines\s*:\s*(\d+)")

# BioLogic EC-Lab .mpt exports always emit these columns in this fixed
# order (freq/Hz, Re(Z)/Ohm, -Im(Z)/Ohm, ...) — used as a positional
# fallback below when a column's NAME can't be trusted. Seen in the wild:
# a file re-saved through Excel where the "-Im(Z)/Ohm" header (leading
# "-" makes Excel treat it as the start of a formula) got mangled into
# literal text like "#NAME?" on save. The data itself is untouched, only
# that one header cell — so falling back to position recovers it exactly.
_MPT_COLUMN_POSITIONS = {"freq/Hz": 0, "Re(Z)/Ohm": 1, "-Im(Z)/Ohm": 2}


def _numeric_column(df: "pd.DataFrame", name: str) -> np.ndarray:
    """
    df[name] as numeric, but if `name` isn't a real column (a corrupted
    header, e.g. Excel turning "-Im(Z)/Ohm" into "#NAME?" on save) fall
    back to that column's known position in a standard EC-Lab export
    instead of raising. Prints a one-line warning when this happens so
    the fallback is visible, not silent.
    """
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").to_numpy()

    position = _MPT_COLUMN_POSITIONS.get(name)
    if position is not None and position < len(df.columns):
        actual = df.columns[position]
        print(
            f"⚠️  Column '{name}' not found (got '{actual}' instead — "
            f"likely a corrupted header, e.g. Excel mangling a leading "
            f"'-' into '#NAME?' on save). Falling back to column "
            f"position {position}, which is always '{name}' in a "
            f"standard EC-Lab export."
        )
        return pd.to_numeric(df.iloc[:, position], errors="coerce").to_numpy()

    raise KeyError(
        f"Column '{name}' not found in EC-Lab export and no positional "
        f"fallback is available (columns: {list(df.columns)})"
    )


def _biologic_header_line_count(lines: List[str]) -> "int | None":
    """
    BioLogic .mpt files declare their own header length up front, e.g.
    "Nb header lines : 61". That count already includes the
    column-names line itself, so the data table starts at line N
    (1-indexed) — i.e. lines[N - 1] is the column-names row and
    lines[N:] are the data rows. Only checked near the top of the file
    (metadata after that can itself contain the digit pattern).
    """
    for line in lines[:5]:
        m = _HEADER_LINE_COUNT_RE.search(line)
        if m:
            return int(m.group(1))
    return None


def is_eis_file(filepath) -> bool:
    """Return True if the file looks like an EIS data file — either the
    project's plain freq/Z txt export, or a BioLogic EC-Lab .mpt export."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline()
        if first_line.strip().lower() == "ec-lab ascii file":
            return True
        header = first_line.lower()
        return ("freq" in header or "frequency" in header) and (
            "ohm" in header or "z" in header
        )
    except Exception:
        return False


def load_eis_runs(filepath) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """
    Load one EIS raw file into a list of (freq, z_real, z_imag) runs.
    Almost always a list of length 1 — the exception is a BioLogic
    .mpt file whose "cycle number" column changes value partway
    through, which means the file actually contains several repeated
    frequency sweeps concatenated together (see module docstring).
    Each is split out and returned as its own run, in cycle-number
    order.
    """
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    header_line_count = _biologic_header_line_count(lines)
    if header_line_count is not None:
        df = pd.read_csv(StringIO("".join(lines[header_line_count - 1:])), sep="\t")
        df.columns = [c.strip() for c in df.columns]
        freq_all = _numeric_column(df, "freq/Hz")
        z_real_all = _numeric_column(df, "Re(Z)/Ohm")
        # EC-Lab exports -Im(Z) (the Nyquist-plot sign — positive for
        # capacitive behaviour). Negate back to true signed Im(Z) to
        # match this project's existing txt-export convention, where
        # the raw Z2 column is already negative for capacitive
        # behaviour (see e.g. Data/EIS/Batch1/*.txt) — everything
        # downstream (plot_eis, fit_eis) additionally self-corrects the
        # sign via _fix_sign_convention(), so this only has to be
        # consistent, not perfect.
        z_imag_all = -_numeric_column(df, "-Im(Z)/Ohm")

        if "cycle number" in df.columns:
            cycle_col = pd.to_numeric(df["cycle number"], errors="coerce").to_numpy()
            cycles = [c for c in pd.unique(cycle_col) if not np.isnan(c)]
            cycles.sort()
        else:
            cycles = []

        if len(cycles) > 1:
            runs = []
            for c in cycles:
                mask = cycle_col == c
                runs.append((freq_all[mask], z_real_all[mask], z_imag_all[mask]))
            return runs

        return [(freq_all, z_real_all, z_imag_all)]

    # Plain project txt export: 3 tab-separated columns, header row 0.
    df = pd.read_csv(filepath, sep="\t", header=0)
    cols = df.columns.tolist()
    freq = df[cols[0]].values
    return [(freq, df[cols[1]].values, df[cols[2]].values)]


def load_eis_file(filepath):
    """
    Load one EIS raw file into a single (freq, z_real, z_imag) spectrum
    — dispatches on content, not extension, so either format works
    regardless of what it's named. Back-compat convenience wrapper
    around load_eis_runs(): for a file with more than one run (a
    multi-cycle BioLogic .mpt), this returns only the first one — use
    load_eis_runs() directly when you need all of them (which is what
    collect_measurements() in parser.py does).
    """
    return load_eis_runs(filepath)[0]


def collect_eis_files(folder) -> List[Path]:
    """
    Collect all EIS files from *folder* (both .txt and BioLogic .mpt),
    sorted by:
      1. Numeric index embedded in the filename (defaultdata_N.txt, a
         BioLogic "_C02" channel suffix, etc.)
      2. Alphabetical order for files without an embedded number.
    """
    folder = Path(folder)
    if not folder.exists():
        return []

    candidates = [*folder.glob("*.txt"), *folder.glob("*.mpt")]
    matched = [f for f in candidates if is_eis_file(f)]

    def _sort_key(p):
        m = re.search(r"(\d+)", p.stem)
        return (0, int(m.group(1))) if m else (1, p.stem)

    return sorted(matched, key=_sort_key)


def earliest_mtime(folder) -> float | None:
    """Earliest file modified-time in *folder* — used as an experiment_date fallback."""
    files = collect_eis_files(folder)
    if not files:
        return None
    return min(f.stat().st_mtime for f in files)
