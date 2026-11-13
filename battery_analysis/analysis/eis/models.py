"""
Data objects: the parser -> repository contract.

An EISMeasurement is one Nyquist sweep (one cycle, one physical cell).
Which cell it belongs to (sample, chemistry, electrolyte, replicate
number, date) is NOT stored here — that's the shared `experiment` row
the importer resolves once per folder and attaches every measurement to.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional

import numpy as np


@dataclass
class EISMeasurement:
    cycle_number: str        # e.g. "10" (raw label — mirrors old CYCLE_MAPPING values)
    sheet_name: Optional[str]  # e.g. "EIS10" — filled in by the importer
    freq: np.ndarray
    z_real: np.ndarray
    z_imag: np.ndarray
    source_file: Optional[Path]


@dataclass
class EISFitResult:
    measurement: EISMeasurement
    circuit_key: str
    params: Dict[str, float]
    method: Literal["model_fit", "freq_extraction"]
