"""
Auto-discovery of samples and batches from the raw data folder structure —
the thing that makes config.yaml override-only instead of a manual folder
inventory.

Convention: a raw data folder name is <sample base name><single replicate
digit>, e.g. "Bare1", "Bare2" -> sample "Bare" (replicates 1, 2); "PA8001",
"PA8002" -> sample "PA800" (replicates 1, 2). Only the LAST digit is taken
as the replicate number (so "PA8001" splits as "PA800"+"1", not "PA"+"8001")
— this matches your actual folder naming, where replicate counts never
exceed single digits. A folder with no trailing digit is its own sample
with replicate 1.

The trailing digit is only ever taken from 1-9, never 0 — every replicate
count in this project starts at 1 (Bare1/Bare2, CFLMP1/CFLMP2, ...), so a
folder ending in "0" (e.g. "LMR850", "LMR900", "LMR950") isn't a
replicate marker, it's part of the sample's own name. Treating that
trailing "0" as replicate "0" was a real bug here: EIS's "LMR850" folder
was silently read as sample "LMR85" replicate 0, so the sample showed up
everywhere (legends, config.yaml/eis.yaml sample_overrides by name, the
database) under the wrong, truncated name, and any override keyed by the
correct "LMR850" silently never matched.

This is deliberately generic (technique-agnostic) — EIS, cycle, rate, DRT,
etc. can all discover their raw layout the same way, since your raw data
folders follow the same naming pattern throughout Data/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

_TRAILING_DIGITS = re.compile(r"^(.*)([1-9])$")

# Stable default palette — used when a discovered sample has no colour
# override in config.yaml. Cycles if there are more samples than colours.
DEFAULT_PALETTE = [
    "#6B7280", "#E8746A", "#5B9BD5", "#70AD47", "#FFC000",
    "#7030A0", "#264478", "#9E480E", "#43682B", "#997300",
]


@dataclass
class DiscoveredReplicate:
    folder_name: str
    replicate_number: int
    path: Path
    n_files: int = 0  # how many raw files this predicate matched — used to pick a default representative


@dataclass
class DiscoveredSample:
    base_name: str
    replicates: List[DiscoveredReplicate] = field(default_factory=list)

    @property
    def folder_names(self) -> List[str]:
        return [r.folder_name for r in self.replicates]

    @property
    def default_representative(self) -> Optional[str]:
        """The replicate with the most matching raw files, as a sensible plotting default."""
        if not self.replicates:
            return None
        return max(self.replicates, key=lambda r: r.n_files).folder_name


def _split_base_and_replicate(folder_name: str) -> tuple[str, int]:
    m = _TRAILING_DIGITS.match(folder_name)
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return folder_name, 1


def discover_samples(
    technique_root: Path,
    file_matches: Callable[[Path], bool],
) -> List[DiscoveredSample]:
    """
    Walk immediate subfolders of technique_root, keep the ones containing
    at least one file for which file_matches(path) is True, and group them
    into samples by the trailing-digit convention.

    file_matches is technique-specific (e.g. analysis.eis.io.is_eis_file)
    so the same discovery logic works for every technique.
    """
    technique_root = Path(technique_root)
    if not technique_root.exists():
        return []

    samples: Dict[str, DiscoveredSample] = {}

    for folder in sorted(p for p in technique_root.iterdir() if p.is_dir()):
        matching_files = [f for f in folder.iterdir() if f.is_file() and file_matches(f)]
        if not matching_files:
            continue

        base_name, replicate_number = _split_base_and_replicate(folder.name)
        sample = samples.setdefault(base_name, DiscoveredSample(base_name=base_name))
        sample.replicates.append(
            DiscoveredReplicate(
                folder_name=folder.name,
                replicate_number=replicate_number,
                path=folder,
                n_files=len(matching_files),
            )
        )

    for sample in samples.values():
        sample.replicates.sort(key=lambda r: r.replicate_number)

    return sorted(samples.values(), key=lambda s: s.base_name)


def discover_batches(
    technique_dir: Path,
    file_matches: Callable[[Path], bool],
) -> List[str]:
    """
    Detect whether technique_dir contains batch subfolders (e.g. Batch1,
    Batch2 — each itself containing sample folders) or sample folders
    directly. Returns a list of batch names, or [None] if there's no
    batch level (samples live directly under technique_dir).
    """
    technique_dir = Path(technique_dir)
    if not technique_dir.exists():
        return [None]

    subfolders = sorted(p for p in technique_dir.iterdir() if p.is_dir())
    if not subfolders:
        return [None]

    # If any immediate subfolder itself contains sample-like folders with
    # matching raw files two levels down, treat this level as batches.
    for folder in subfolders:
        inner = [p for p in folder.iterdir() if p.is_dir()] if folder.is_dir() else []
        for inner_folder in inner:
            if any(f.is_file() and file_matches(f) for f in inner_folder.iterdir()):
                return [p.name for p in subfolders]

    return [None]


def color_for(index: int, palette: Optional[List[str]] = None) -> str:
    pal = palette or DEFAULT_PALETTE
    return pal[index % len(pal)]
