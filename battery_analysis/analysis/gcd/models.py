"""Data objects for GCD."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class GCDTrace:
    sample_name: str
    time_h: np.ndarray
    voltage_v: np.ndarray
    source_file: Path
    current_density_mA_cm2: Optional[float] = None


@dataclass
class GCDAnomaly:
    cycle: int
    phase: str
    time_h: float
    voltage_v: float
    delta_v: float
