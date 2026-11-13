"""Shared data objects for Cycle and Rate."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class CyclingSeries:
    """One sample's per-cycle capacity/CE series, read from one *_Capacity.csv."""
    sample_name: str
    cycle_number: np.ndarray
    capacity_charge: np.ndarray
    capacity_discharge: np.ndarray
    ce_pct: np.ndarray
    source_file: Path
