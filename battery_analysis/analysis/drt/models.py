"""Data objects for DRT."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np


@dataclass
class DRTPeak:
    label: str
    description: str
    tau_peak: float
    gamma_peak: float
    log_tau: float
    frequency_hz: float


@dataclass
class DRTResult:
    tau: np.ndarray
    gamma: np.ndarray
    r_inf: float
    l_ind: float
    peaks: List[DRTPeak] = field(default_factory=list)
