"""
Shared by Cycle and Rate: both read the same raw file format — a pair of
"*_Capacity.csv" (per-cycle Q_Charge/Q_Discharge summary) and
"*_Cycle.csv" (full voltage/capacity profile, multi-level header) per
sample. This module owns that raw reading; io.py stays technique-agnostic
so Cycle and Rate never duplicate it (per your request to eliminate
repetition between them).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd


class CyclingFileFormatError(ValueError):
    """
    Raised when a *_Capacity.csv is missing a column read_capacity_csv()
    needs, even after the whitespace/case-insensitive matching below.
    Callers catch this specifically and skip the one bad file with a
    clear warning instead of the whole batch import crashing on a raw
    pandas KeyError — same self-healing-over-crashing principle as the
    EC-Lab column fallback in analysis/eis/io.py.
    """
    def __init__(self, file_path, missing_column, found_columns):
        self.file_path = file_path
        self.missing_column = missing_column
        self.found_columns = found_columns
        super().__init__(
            f"{Path(file_path).name}: expected a column matching "
            f"{missing_column!r} but found columns {list(found_columns)!r}"
        )


def is_capacity_csv(filepath) -> bool:
    return Path(filepath).name.endswith("_Capacity.csv")


def _normalize(name: str) -> str:
    """Collapse all whitespace and lowercase, so 'Q_Dis/M [mAh/g]',
    'Q_Dis/M[mAh/g]', and 'q_dis/m [mah/g]' all match the same column —
    different testers/export settings are inconsistent about spacing
    and case around this exact header, not about the header itself."""
    return re.sub(r"\s+", "", name).strip().lower()


def _find_column(df: pd.DataFrame, expected_name: str, file_path) -> str:
    """Return the actual column name in df matching expected_name after
    whitespace/case normalization, or raise CyclingFileFormatError with
    the real column list if nothing matches."""
    target = _normalize(expected_name)
    for col in df.columns:
        if _normalize(str(col)) == target:
            return col
    raise CyclingFileFormatError(file_path, expected_name, list(df.columns))


def read_capacity_csv(file_path) -> pd.DataFrame:
    """
    Load a *_Capacity.csv summary (Cycle, Q_Charge, Q_Discharge per row).

    Ported verbatim from cycle.py's load_cycle_data — cathode convention:
    CE = Q_Discharge / Q_Charge x 100 (discharge = Li extraction from
    cathode = the useful delivered capacity).

    Column lookup is whitespace/case-insensitive (see _find_column) so a
    tester export with slightly different header spacing/casing still
    parses; a genuinely missing column raises CyclingFileFormatError
    instead of a bare KeyError, so callers can catch it and skip just
    that one file.
    """
    df = pd.read_csv(file_path, encoding="latin1")
    df.columns = [c.strip() for c in df.columns]
    cycle_col = _find_column(df, "Cycle", file_path)
    q_dis_col = _find_column(df, "Q_Dis/M [mAh/g]", file_path)
    q_ch_col = _find_column(df, "Q_Ch/M [mAh/g]", file_path)
    df["Cycle"] = pd.to_numeric(df[cycle_col], errors="coerce")
    df["Q_Discharge"] = pd.to_numeric(df[q_dis_col], errors="coerce")
    df["Q_Charge"] = pd.to_numeric(df[q_ch_col], errors="coerce")
    df = df.dropna(subset=["Cycle", "Q_Discharge", "Q_Charge"]).reset_index(drop=True)
    if df.empty:
        return df
    df["CE (%)"] = 100 * df["Q_Discharge"] / df["Q_Charge"]
    df["Retention (%)"] = 100 * df["Q_Discharge"] / df["Q_Discharge"].iloc[0]
    return df


def load_profile_csv(path) -> pd.DataFrame:
    """
    Load the companion "*_Cycle.csv" voltage/capacity profile file
    (multi-level header: "1st Ch | V [V]", "1st Ch | Q [mAh]", ...).
    Ported verbatim from cycle.py's load_cycle_csv_ordinal.
    """
    df = pd.read_csv(path, header=[0, 1], engine="python")
    df.columns = [f"{a.strip()} | {b.strip()}" for a, b in df.columns]
    return df


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def extract_cycle_profile(profile_path, cycle_num: int, mode: str):
    """
    Pull one ordinal cycle's raw voltage/capacity trace out of a
    *_Cycle.csv profile file. mode: "charge" | "discharge". Cathode
    convention (no instrument-mode swap — that's an anode-only concern,
    same as rate.py). Returns (voltage, capacity) arrays in ORIGINAL
    measurement order (not sorted — sorting destroys the physical
    charge/discharge trajectory), or (None, None) if this cycle/mode
    isn't present in the file (e.g. past the cell's last cycle).

    Shared by dQ/dV (which differentiates the raw trace further) and the
    Cycle/Rate voltage-vs-capacity profile plots (which use it directly).
    """
    instrument_mode = "Ch" if mode.lower() in ("charge", "ch") else "Dis"
    ord_str = ordinal(cycle_num)

    df = pd.read_csv(profile_path, header=[0, 1], engine="python")
    new_cols = []
    for col in df.columns:
        parts = [str(c).strip() for c in col] if isinstance(col, tuple) else [str(col).strip()]
        new_cols.append(" | ".join(parts))
    df.columns = new_cols

    possible_v = [f"{ord_str} {instrument_mode} | V [V]", f"{ord_str} {instrument_mode}|V [V]"]
    possible_q = [f"{ord_str} {instrument_mode} | Q/M [mAh/g]", f"{ord_str} {instrument_mode}|Q/M [mAh/g]"]

    v_col = next((v for v in possible_v if v in df.columns), None)
    q_col = next((q for q in possible_q if q in df.columns), None)
    if v_col is None or q_col is None:
        return None, None

    voltage = pd.to_numeric(df[v_col], errors="coerce").to_numpy()
    capacity = pd.to_numeric(df[q_col], errors="coerce").to_numpy()
    mask = ~(np.isnan(voltage) | np.isnan(capacity))
    voltage, capacity = voltage[mask], capacity[mask]
    if len(voltage) == 0:
        return None, None
    return voltage, capacity
