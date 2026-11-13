"""Data objects for dQ/dV."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class DQDVCurve:
    sample_name: str
    cycle_number: int
    mode: str
    voltage: np.ndarray
    dqdv: np.ndarray
    source_file: Path


@dataclass
class DQDVPeak:
    voltage: float
    intensity: float
