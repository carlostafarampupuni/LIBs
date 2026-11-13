"""
Auto-discovery for Cycle/Rate raw files. Unlike EIS (one folder per
replicate), Cycle/Rate data is one FILE per replicate, with the sample
name and replicate number embedded in the filename:

    LMR900-Cycle-Bare-2_002_Capacity.csv   -> sample "Bare",  replicate 2
    Cycle-1C-PA800-1_003_Capacity.csv       -> sample "PA800", replicate 1
    LMR900-Rate-PA77-1_017_Capacity.csv     -> sample "PA77",  replicate 1

The instrument-run prefix ("LMR900-Cycle-", "Cycle-1C-", "Rate-", ...)
varies and is ignored; only the "<sep><Sample>-<replicate>_<id>_Capacity.csv"
tail is matched, where <sep> is "-" or "_" (some runs separate the prefix
from the sample name with an underscore instead of a dash, e.g.
"LFP_Cycle_MXAl-2_013_Capacity.csv" -> sample "MXAl", replicate 2).

A sample name may also start with a percentage and an underscore, the way
config.yaml names composition series ("Rate-2.5%_SiO2-1_001_Capacity.csv"
-> sample "2.5%_SiO2", replicate 1). The digits-then-"%" part is required
for that, so names like "MXAl" above still split exactly as before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_CAPACITY_PATTERN = re.compile(r"[-_]((?:\d+(?:\.\d+)?%_)?[A-Za-z]+\d*)-(\d+)_\d+_Capacity\.csv$")


@dataclass
class DiscoveredCyclingFile:
    sample_name: str
    replicate_number: int
    capacity_path: Path
    profile_path: Optional[Path]   # the matching "*_Cycle.csv", if present


@dataclass
class DiscoveredCyclingSample:
    name: str
    files: List[DiscoveredCyclingFile] = field(default_factory=list)

    @property
    def default_representative(self) -> Optional[DiscoveredCyclingFile]:
        """First replicate by number — deterministic, override with sample_overrides if wrong."""
        if not self.files:
            return None
        return min(self.files, key=lambda f: f.replicate_number)


def representative_file(sample: DiscoveredCyclingSample, experiments_df) -> Optional[DiscoveredCyclingFile]:
    """The file of this sample's experiment flagged is_representative at import time
    (the importer's choice — see rate.yaml representative:), else the lowest replicate."""
    rows = experiments_df[(experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)]
    if not rows.empty:
        name = rows.iloc[0]["source_folder"]
        for f in sample.files:
            if f.capacity_path.name == name:
                return f
    return sample.default_representative


def discover_cycling_files(data_dir) -> List[DiscoveredCyclingSample]:
    """Scan data_dir for *_Capacity.csv files and group them into samples."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []

    samples: Dict[str, DiscoveredCyclingSample] = {}

    for capacity_path in sorted(data_dir.glob("*_Capacity.csv")):
        m = _CAPACITY_PATTERN.search(capacity_path.name)
        if not m:
            continue
        sample_name, replicate_number = m.group(1), int(m.group(2))

        profile_path = capacity_path.with_name(
            capacity_path.name.replace("_Capacity.csv", "_Cycle.csv")
        )

        sample = samples.setdefault(sample_name, DiscoveredCyclingSample(name=sample_name))
        sample.files.append(
            DiscoveredCyclingFile(
                sample_name=sample_name,
                replicate_number=replicate_number,
                capacity_path=capacity_path,
                profile_path=profile_path if profile_path.exists() else None,
            )
        )

    for sample in samples.values():
        sample.files.sort(key=lambda f: f.replicate_number)

    return sorted(samples.values(), key=lambda s: s.name)
